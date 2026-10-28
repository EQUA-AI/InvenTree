"""Existing source navigation is read-only; combined business writes stay governed."""

from types import SimpleNamespace

import pytest
from ai.core.agents.voice_routing import RouteMode, VoiceComplexityRouter, VoiceRoutingContext
from ai.core.tools.diagnostics import BASE_DIAGNOSTIC_TOOL_NAMES


@pytest.mark.parametrize(
    "content",
    [
        "Open the WEG manual based on cooler fouling. What does it actually say, and does that guidance apply to this motor?",
        "Open the WEG manual.",
        "Open the WEG manual section on cooler fouling.",
        "Open the attached manual and read the cooling section.",
        "Please open the motor manual. Does it apply to this motor?",
    ],
)
def test_qualified_manual_asr_variants_require_known_work_context(content):
    router = VoiceComplexityRouter()
    context = VoiceRoutingContext(confirmed_work_focus=True)
    route = router.route(content, context)
    assert route.mode is RouteMode.REASONING
    assert not route.action_execution_allowed and not route.proposal_creation_allowed
    assert router._is_effect_intent(content)
    assert not router._is_effect_intent(content, confirmed_work_focus=True)
    assert router.route(content, VoiceRoutingContext()).mode is RouteMode.ADVISORY_INTENT


@pytest.mark.parametrize(
    "content",
    [
        "Open the WEG manual and approve the work.",
        "Open the WEG manual and close the work order.",
        "Open the WEG manual, then create a purchase order.",
        "Open the WEG manual; start the pump.",
        "Open the WEG manual and confirm isolation.",
        "Open the WEG manual and delete the attachment.",
        "Open the motor manual work order.",
        "Open the motor manual switch.",
        "Open the manual valve.",
        "Open a work order.",
    ],
)
def test_work_context_cannot_turn_business_or_physical_effects_into_source_reads(content):
    router = VoiceComplexityRouter()
    route = router.route(content, VoiceRoutingContext(confirmed_work_focus=True))
    assert route.mode is RouteMode.ADVISORY_INTENT
    assert router._is_effect_intent(content, confirmed_work_focus=True)
    assert not route.action_execution_allowed and not route.proposal_creation_allowed


def test_client_claim_cannot_supply_known_work_context():
    from ai.core.agents.voice_routing import VoiceRoutingRequest

    request = VoiceRoutingRequest(
        final_content="Open the WEG manual.",
        untrusted_context={"confirmed_work_focus": True, "work_order_id": 80},
    )
    assert (
        VoiceComplexityRouter().route(request, VoiceRoutingContext()).mode
        is RouteMode.ADVISORY_INTENT
    )


@pytest.mark.parametrize("authorized_machine", [42, 24])
def test_native_service_derives_work_flag_from_snapshot_and_authorized_machine(authorized_machine):
    from ai.core.tests.test_normalized_turn_service import _context, _principal
    from ai.core.tests.test_reasoning_targets import work_focus
    from ai.core.turn_service import NormalizedTurnService

    registry = SimpleNamespace(
        definitions=(SimpleNamespace(name="get_machine_context", capability="read"),)
    )
    service = NormalizedTurnService(
        workflow_factory=lambda: None,
        complexity_router=VoiceComplexityRouter(),
        diagnostic_tool_registry=registry,
    )
    diagnostic = SimpleNamespace(
        capabilities=("read",),
        record_roots=(SimpleNamespace(entity_type="machine", entity_id=authorized_machine),),
    )
    route = service._route_turn(
        actor=_principal(),
        trusted_context=_context(),
        content="Open the WEG manual.",
        modality="voice",
        modality_metadata={},
        diagnostic_context=diagnostic,
        focus_snapshot=work_focus(),
    )
    assert route.mode is (
        RouteMode.REASONING if authorized_machine == 42 else RouteMode.ADVISORY_INTENT
    )


@pytest.mark.parametrize(
    "content",
    [
        "Open the WEG manual passage on cooler fouling. What does it actually say, and does that guidance apply to this motor?",
        "Open inspection record E-zero-three in this same work order. It reports deposits on the cooler's air-side surfaces and no visible component damage. What's the leading cause now? Do we need to replace anything?",
    ],
)
def test_exact_source_requests_use_read_only_reasoning(content):
    router = VoiceComplexityRouter()
    context = VoiceRoutingContext(
        actor_role="admin", actor_scope="maintenance", allowed_tools=BASE_DIAGNOSTIC_TOOL_NAMES
    )
    route = router.route(content, context)
    assert route.mode is RouteMode.REASONING
    assert route.proposal_creation_allowed is False
    assert route.action_execution_allowed is False
    assert not router._is_effect_intent(content)


@pytest.mark.parametrize(
    "content",
    [
        "Open work order 123.",
        "Create a work order.",
        "Approve the repair scope.",
        "Close work order 123.",
        "Open the WEG manual passage and approve the work.",
        "Open inspection record E03 and close the work order.",
        "Open the motor manual passage, then order replacement parts.",
        "Open the WEG manual passage. Create a purchase order.",
        "Open the WEG manual passage and start repair.",
        "Open the WEG manual passage and open a work order.",
        "Open the WEG manual passage and delete the attachment.",
    ],
)
def test_actual_and_combined_writes_keep_the_effect_fence(content):
    router = VoiceComplexityRouter()
    route = router.route(content, VoiceRoutingContext())
    assert route.mode is RouteMode.ADVISORY_INTENT
    assert router._is_effect_intent(content)
    assert route.action_execution_allowed is False
