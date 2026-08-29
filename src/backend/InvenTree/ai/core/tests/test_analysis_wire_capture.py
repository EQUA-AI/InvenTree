"""S10 WP-A6 (§13.2 wire clause): forbidden content is NEVER emitted.

Not merely absent from the stored final message — these tests capture every
event the analysis branch puts on the wire (classic SSE dialect), replay
the capture through the AG-UI translator, and assert the poison appears on
neither channel, in no event, under every gate mode.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis import executor as executor_module
from ai.core.analysis.schemas import SynthesisClaimSet
from ai.core.tests.test_analysis_executor import (
    RecordingEmitter,
    _fake_records,
)
from ai.core.turn.execution import _run_analysis_branch
from ai.core.turn_service import NormalizedTurnService

POISON = "the bearing has catastrophically failed and WO-9999 proves it"


def _service():
    async def _call_sync(fn, *args, **kwargs):  # noqa: RUF029 - service contract is async
        return fn(*args, **kwargs)

    return SimpleNamespace(
        _call_sync=_call_sync,
        _rehydrate_user_for_grounding=lambda _actor: SimpleNamespace(pk=5, is_authenticated=True),
        _emit_canonical_events=NormalizedTurnService._emit_canonical_events,
    )


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
        route=SimpleNamespace(to_dict=lambda: {"mode": "analysis"}),
    )


def _poisoned_synthesis(*args, **kwargs) -> SynthesisClaimSet:
    return SynthesisClaimSet.model_validate_json(
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
                    "paraphrase": POISON[:240],
                }
            ],
            "assumptions": [],
            "unknowns": [],
        })
    )


def _settings(mode: str):
    return SimpleNamespace(
        evidence_gate_mode=mode,
        analysis_turn_deadline_s=5.0,
        analysis_synthesis_timeout_s=0.5,
        feature_tool_events=False,
    )


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(executor_module, "_retrieve_records", _fake_records)
    monkeypatch.setattr(executor_module, "synthesize_claims", lambda *_a, **_k: None)
    monkeypatch.setattr(executor_module, "_reauthorize", lambda _user, _store: True)


def _branch(mode: str, *, emitter, synthesize=None, monkeypatch=None):
    if synthesize is not None and monkeypatch is not None:
        monkeypatch.setattr(executor_module, "synthesize_claims", synthesize)
    run = _run(emitter=emitter)
    with mock.patch("ai.core.config.get_settings", lambda: _settings(mode)):
        canonical = asyncio.run(_run_analysis_branch(_service(), run))
    return canonical, run


def _translate_all(events: list[dict]) -> str:
    """Replay the captured classic events through the AG-UI translator."""
    from ai.core.agui.translate import SpecTranslator

    translator = SpecTranslator(thread_id="thread_req", run_id="run_req")
    out: list[dict] = []
    for event in events:
        record = {**event, "type": str(event.get("event_type") or event.get("type"))}
        record.pop("event_type", None)
        out.extend(translator.translate(record))
    return json.dumps(out)


def test_poisoned_synthesis_never_reaches_any_wire(monkeypatch) -> None:
    """The downgrade happened BEFORE emission: no channel ever saw the poison."""
    emitter = RecordingEmitter()
    canonical, _run_obj = _branch(
        "enforce", emitter=emitter, synthesize=_poisoned_synthesis, monkeypatch=monkeypatch
    )

    classic_blob = json.dumps(emitter.events)
    assert "catastrophically" not in classic_blob
    assert "WO-9999" not in classic_blob
    agui_blob = _translate_all(emitter.events)
    assert "catastrophically" not in agui_blob
    assert "WO-9999" not in agui_blob
    # The stored canonical (voice-resync surface included) is clean too.
    canonical_blob = json.dumps(canonical)
    assert "catastrophically" not in canonical_blob
    assert canonical["response_state"] == "incomplete"
    assert "no conclusion was produced" in canonical["message"].lower()


def test_enforce_serves_the_validated_v2_with_the_attachment(monkeypatch) -> None:
    emitter = RecordingEmitter()
    canonical, run = _branch("enforce", emitter=emitter)

    assert canonical["workflow_used"] == "analysis_executor"
    assert canonical["response_state"] == "complete"
    assert canonical["canonical_response"]["response_version"] == 2
    assert canonical["evidence_analysis"]["claims"]
    assert run.extras["evidence_sets"]

    # Progress precedes the buffered lifecycle; exactly one text event.
    content_events = [
        event
        for event in emitter.events
        if "TEXT_MESSAGE_CONTENT" in str(event.get("type") or event.get("event_type"))
    ]
    assert len(content_events) == 1
    # to_dict() flattens the payload into the SSE record.
    data = content_events[0].get("data") or content_events[0]
    assert "2 matching records" in json.dumps(data)
    deltas = [
        (event.get("data") or event).get("kind")
        for event in emitter.events
        if "STATE_DELTA" in str(event.get("type") or event.get("event_type"))
    ]
    assert deltas.count("evidence_analysis") == 1
    assert deltas.count("analysis_progress") == 3
    # AG-UI parity: the attachment rides its dedicated channel.
    agui = _translate_all(emitter.events)
    assert "aimms.evidenceAnalysis" in agui
    assert "aimms.analysisProgress" in agui


def test_shadow_serves_the_abstention_and_persists_the_verdict() -> None:
    emitter = RecordingEmitter()
    canonical, run = _branch("shadow", emitter=emitter)

    assert canonical["workflow_used"] == "analysis_unavailable"
    assert canonical["evidence_gate"]["mode"] == "shadow_rehearsal"
    assert canonical["evidence_gate"]["verdict"] == "pass"
    assert run.validation_result is not None
    # Dark rehearsal: nothing but the abstention lifecycle on the wire.
    blob = json.dumps(emitter.events)
    assert "evidence_analysis" not in blob
    assert "analysis_progress" not in blob
    assert "2 matching records" not in blob


def test_gate_off_is_byte_identical_abstention() -> None:
    emitter = RecordingEmitter()
    canonical, run = _branch("off", emitter=emitter)
    assert canonical["workflow_used"] == "analysis_unavailable"
    assert "evidence_gate" not in canonical
    assert "evidence_analysis" not in canonical
    assert run.extras == {}


def test_unrouted_intents_defensively_abstain() -> None:
    # Routing never sends an unshipped intent here (it keeps the legacy
    # rail); reaching this branch requires a settings race between routing
    # and dispatch. The defensive tail must be an honest abstention under
    # EVERY gate mode — and the typed "capability boundary" refusal no
    # longer exists (owner 2026-08-29).
    for gate_mode in ("shadow", "off", "enforce"):
        with mock.patch("ai.core.config.get_settings", lambda m=gate_mode: _settings(m)):
            canonical = asyncio.run(
                _run_analysis_branch(
                    _service(), _run(intent="fleet_aggregate", emitter=RecordingEmitter())
                )
            )
        assert canonical["workflow_used"] == "analysis_unavailable", gate_mode
        assert "analysis_capability_boundary" not in str(canonical)


def test_legacy_rail_runs_the_shadow_prose_scan() -> None:
    """WP-A7 wiring pin: the soak lives on the legacy rail (real traffic),
    gated on ANALYSIS intents, persisted beside the grounding blob."""
    import inspect

    from ai.core.turn.execution import _run_legacy_workflow

    source = inspect.getsource(_run_legacy_workflow)
    assert "shadow_scan_legacy" in source
    assert "ANALYSIS_INTENTS" in source
    assert 'canonical["evidence_gate"]' in source
    # The scan reads the ledger's server-shown closure, never re-deriving.
    assert "observed_values()" in source
