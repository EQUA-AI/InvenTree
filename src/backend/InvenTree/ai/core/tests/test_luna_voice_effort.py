"""A server voice-only inference setting cannot change text or effect authority."""

import asyncio
from types import SimpleNamespace

import pytest
from ai.core.agents.voice_routing import (
    ReasoningEffort,
    RouteMode,
    RouteReason,
    VoiceRouteDecision,
)
from ai.core.config import Settings
from ai.core.reasoning.schemas import CanonicalTurnResponse
from ai.core.streaming import InMemoryEventEmitter
from ai.core.tests.test_luna_diagnostics import _canonical_json
from ai.core.tests.test_normalized_turn_service import _context, _principal
from ai.core.turn_service import NormalizedTurnService
from pydantic import ValidationError


def test_voice_effort_default_preserves_routing_and_rejects_low():
    assert Settings(_env_file=None).azure_luna_voice_reasoning_effort == ""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, azure_luna_voice_reasoning_effort="low")


@pytest.mark.parametrize(
    ("modality", "override", "expected"),
    [
        ("voice", "", "high"),
        ("voice", "medium", "medium"),
        ("voice", "high", "high"),
        ("text", "medium", "high"),
    ],
)
def test_only_voice_read_reasoning_uses_explicit_server_effort(
    monkeypatch, modality, override, expected
):
    calls = []

    class Adapter:
        async def reason(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                response=CanonicalTurnResponse.model_validate_json(_canonical_json()),
                provenance=SimpleNamespace(to_dict=lambda: {"effort": kwargs["effort"]}),
            )

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: SimpleNamespace(azure_luna_voice_reasoning_effort=override),
    )
    definition = SimpleNamespace(name="get_machine_context", capability="read")
    service = NormalizedTurnService(
        workflow_factory=lambda: None,
        reasoning_adapter=Adapter(),
        diagnostic_tool_registry=SimpleNamespace(definitions=(definition,)),
    )
    diagnostic = SimpleNamespace(capabilities=("read",), record_roots=())
    route = VoiceRouteDecision(
        mode=RouteMode.REASONING,
        effort=ReasoningEffort.HIGH,
        reason_codes=(RouteReason.MANUAL_HISTORY_COMPARISON,),
        target_workflow_id="wf1",
    )
    result = asyncio.run(
        service._reasoning_canonical(
            actor=_principal(),
            trusted_context=_context(),
            thread_id="thread-test",
            turn_id="test-turn",
            content="Read the diagnostic sources.",
            modality=modality,
            route=route,
            diagnostic_context=diagnostic,
            emitter=InMemoryEventEmitter(),
        )
    )

    assert calls[0]["effort"] == expected
    assert result["route"]["effort"] == expected
    assert result["route"]["action_execution_allowed"] is False
    assert result["route"]["proposal_creation_allowed"] is False
