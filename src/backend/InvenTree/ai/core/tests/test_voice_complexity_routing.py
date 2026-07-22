"""Contract tests for deterministic voice complexity routing."""

from __future__ import annotations

import sys
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import ai.core
import pytest

# The workflows package eagerly imports provider implementations.  These unit
# tests exercise only the registry and must remain provider/network-free.
_workflows_package = types.ModuleType("ai.core.workflows")
_workflows_package.__path__ = [str(Path(ai.core.__file__).resolve().parent / "workflows")]
sys.modules.setdefault("ai.core.workflows", _workflows_package)

from ai.core.agents.routing import WorkflowType  # noqa: E402
from ai.core.agents.voice_routing import (  # noqa: E402
    ReasoningEffort,
    RiskLevel,
    RouteMode,
    RouteReason,
    VoiceComplexityRouter,
    VoiceRoutingContext,
    VoiceRoutingRequest,
)
from ai.core.workflows.registry import (  # noqa: E402
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowTier,
)


def trusted_context(**overrides) -> VoiceRoutingContext:
    """Return a typical server-owned routing context for tests."""
    values = {
        "actor_role": "technician",
        "actor_scope": "site:main",
        "transcription_confidence": 0.98,
        "risk": RiskLevel.LOW,
    }
    values.update(overrides)
    return VoiceRoutingContext(**values)


@pytest.mark.parametrize(
    ("content", "reason"),
    (
        ("Hello", RouteReason.GREETING),
        ("What can you do?", RouteReason.HELP),
        ("Thanks", RouteReason.ACKNOWLEDGEMENT),
        ("Show repair orders for this week", RouteReason.SIMPLE_LOOKUP),
        ("What does error code E04 mean?", RouteReason.SIMPLE_FACT),
    ),
)
def test_social_lookup_and_fact_turns_stay_fast(content, reason) -> None:
    """Simple final content remains on the fast path."""
    decision = VoiceComplexityRouter().route(content, trusted_context())

    assert decision.mode is RouteMode.FAST_PATH
    assert decision.effort is ReasoningEffort.LOW
    assert decision.reason_codes == (reason,)
    assert decision.target_workflow_id == "wf8"


def test_repair_keyword_alone_does_not_select_reasoning() -> None:
    """The word repair is not sufficient evidence of repair planning."""
    router = VoiceComplexityRouter()

    lookup = router.route("Show repair records for line 4", trusted_context())
    planning = router.route("Develop a repair plan for the vibrating pump", trusted_context())

    assert lookup.mode is RouteMode.FAST_PATH
    assert RouteReason.REPAIR_PLANNING not in lookup.reason_codes
    assert planning.mode is RouteMode.REASONING
    assert RouteReason.REPAIR_PLANNING in planning.reason_codes


@pytest.mark.parametrize(
    "content",
    (
        "Update me on the repair status",
        "Complete repair history for pump seven",
        "Start date for repair order 42",
    ),
)
def test_effect_words_in_informational_phrases_do_not_select_advisory(
    content,
) -> None:
    """Effect verbs require intent syntax, not mere keyword presence."""
    decision = VoiceComplexityRouter().route(content, trusted_context())

    assert decision.mode is RouteMode.FAST_PATH
    assert RouteReason.EFFECT_INTENT not in decision.reason_codes


@pytest.mark.parametrize(
    "content",
    (
        "Diagnose the pump vibration",
        "What are the possible causes of the leak?",
        "Develop a repair plan for the stalled conveyor",
        "The sensor readings are inconsistent with the inspection",
        "Compare the service manual with its maintenance history",
    ),
)
def test_diagnostic_and_evidence_content_routes_to_reasoning(content) -> None:
    """Explicit complex content selects the canonical diagnostics workflow."""
    decision = VoiceComplexityRouter().route(content, trusted_context())

    assert decision.mode is RouteMode.REASONING
    assert decision.target_workflow_id == "wf1"
    assert decision.effort in set(ReasoningEffort)


def test_untrusted_authority_claims_do_not_change_route() -> None:
    """Client workflow, identity, capability, and risk claims are inert."""
    router = VoiceComplexityRouter()
    baseline = router.route(
        VoiceRoutingRequest(final_content="Show repair orders"), trusted_context()
    )
    attacked = router.route(
        VoiceRoutingRequest(
            final_content="Show repair orders",
            workflow_hint="wf4",
            client_id="admin:1",
            client_capabilities=("inventory.write", "approval.execute"),
            untrusted_context={
                "risk": "critical",
                "workflow_hint": "wf1",
                "allowed_tools": ["diagnostics.execute"],
            },
        ),
        trusted_context(),
    )

    assert attacked == baseline
    assert attacked.mode is RouteMode.FAST_PATH
    assert attacked.proposal_creation_allowed is False
    assert attacked.action_execution_allowed is False


def test_trusted_risk_and_confidence_change_the_same_content() -> None:
    """Trusted uncertainty and elevated risk promote an ambiguous turn."""
    router = VoiceComplexityRouter()
    content = "Inspect pump seven"

    normal = router.route(content, trusted_context())
    risky = router.route(content, trusted_context(risk=RiskLevel.HIGH))
    uncertain = router.route(content, trusted_context(transcription_confidence=0.42))

    assert normal.mode is RouteMode.FAST_PATH
    assert normal.effort is ReasoningEffort.MEDIUM
    assert risky.mode is RouteMode.REASONING
    assert risky.effort is ReasoningEffort.HIGH
    assert risky.reason_codes == (RouteReason.ELEVATED_RISK,)
    assert uncertain.mode is RouteMode.REASONING
    assert uncertain.effort is ReasoningEffort.HIGH
    assert uncertain.reason_codes == (RouteReason.LOW_TRANSCRIPTION_CONFIDENCE,)


def test_ambiguous_turn_escalates_when_policy_opts_in() -> None:
    """Max-caution policy routes an unclassified turn up to reasoning."""
    from ai.core.agents.voice_routing import VoiceRoutingPolicy

    router = VoiceComplexityRouter()
    content = "Inspect pump seven"

    # Default policy keeps the deliberate read-only fast-path behavior.
    assert router.route(content, trusted_context()).mode is RouteMode.FAST_PATH

    cautious_policy = VoiceRoutingPolicy(escalate_ambiguous_to_reasoning=True)
    cautious = router.route(content, trusted_context(policy=cautious_policy))
    assert cautious.mode is RouteMode.REASONING
    assert cautious.reason_codes == (RouteReason.GENERAL_REQUEST,)

    # Explicit benign lookups still take the fast lane even under max caution.
    lookup = router.route("Show repair order 42", trusted_context(policy=cautious_policy))
    assert lookup.mode is RouteMode.FAST_PATH


def test_explicit_lookup_stays_fast_while_context_raises_effort() -> None:
    """Trusted risk and uncertainty do not turn a simple lookup diagnostic."""
    decision = VoiceComplexityRouter().route(
        "Show repair order 42",
        trusted_context(
            risk=RiskLevel.HIGH,
            transcription_confidence=0.42,
        ),
    )

    assert decision.mode is RouteMode.FAST_PATH
    assert decision.effort is ReasoningEffort.HIGH
    assert decision.reason_codes == (
        RouteReason.SIMPLE_LOOKUP,
        RouteReason.LOW_TRANSCRIPTION_CONFIDENCE,
        RouteReason.ELEVATED_RISK,
    )


def test_trusted_diagnostic_tools_materially_raise_effort() -> None:
    """Available evidence tools deepen an already diagnostic route."""
    router = VoiceComplexityRouter()
    content = "Diagnose the pump vibration"

    without_tools = router.route(content, trusted_context())
    with_tools = router.route(
        content,
        trusted_context(allowed_tools=("machine.telemetry.read", "maintenance_history.read")),
    )

    assert without_tools.mode is with_tools.mode is RouteMode.REASONING
    assert without_tools.effort is ReasoningEffort.MEDIUM
    assert with_tools.effort is ReasoningEffort.HIGH
    assert RouteReason.DIAGNOSTIC_EVIDENCE_AVAILABLE in with_tools.reason_codes


def test_actor_role_and_scope_add_caution_without_granting_authority() -> None:
    """Trusted limited actor context affects effort, never permission."""
    router = VoiceComplexityRouter()
    content = "Diagnose the pump vibration"

    technician = router.route(content, trusted_context())
    viewer = router.route(content, trusted_context(actor_role="viewer", actor_scope=""))

    assert technician.effort is ReasoningEffort.MEDIUM
    assert viewer.effort is ReasoningEffort.HIGH
    assert RouteReason.LIMITED_ACTOR_CONTEXT in viewer.reason_codes
    assert viewer.proposal_creation_allowed is False
    assert viewer.action_execution_allowed is False


@pytest.mark.parametrize(
    "content",
    (
        "Create a repair task for the pump",
        "Please update the stock count",
        "Can you approve this work order?",
        "Consume two units from inventory",
        "Start the repair procedure",
        "Complete this maintenance step",
        "Send an email to the supervisor",
        "Archive the kanban card",
        "Delete the card permanently",
        "Order ten replacement bearings",
        "Hold work order WO-42",
        "Resume work order WO-42",
        "Repair the pump",
        "Could you fix the stalled conveyor?",
    ),
)
def test_effect_wording_is_advisory_only(content) -> None:
    """Effect language cannot select or authorize an action workflow."""
    decision = VoiceComplexityRouter().route(
        VoiceRoutingRequest(
            final_content=content,
            workflow_hint="wf4",
            client_capabilities=("approval.execute",),
        ),
        trusted_context(allowed_capabilities=("inventory.write", "approval.execute")),
    )

    assert decision.mode is RouteMode.ADVISORY_INTENT
    assert decision.advisory_only is True
    assert decision.target_workflow_id is None
    assert decision.proposal_creation_allowed is False
    assert decision.action_execution_allowed is False
    assert decision.reason_codes[0] is RouteReason.EFFECT_INTENT


def test_decision_is_immutable_and_exposes_codes_not_hidden_reasoning() -> None:
    """Routing records contain only bounded enums and safe reason codes."""
    decision = VoiceComplexityRouter().route("Diagnose the pump vibration", trusted_context())

    with pytest.raises(FrozenInstanceError):
        decision.effort = ReasoningEffort.LOW
    assert set(decision.to_dict()) == {
        "mode",
        "effort",
        "reason_codes",
        "target_workflow_id",
        "proposal_creation_allowed",
        "action_execution_allowed",
    }
    assert not hasattr(decision, "reasoning")


def test_diagnostics_public_name_and_registry_id_mismatch_is_frozen() -> None:
    """The public T6 value resolves to the canonical wf1 registration."""

    class DiagnosticsWorkflow:
        pass

    registry = WorkflowRegistry()
    definition = WorkflowDefinition(
        workflow_id="wf1",
        name="Diagnostics",
        description="Test diagnostics workflow",
        tier=WorkflowTier.T6_MAGENTIC,
        builder=DiagnosticsWorkflow,
    )
    registry.register(definition)

    assert WorkflowType.T6_DIAGNOSTICS.value == "wf1_diagnostics"
    assert WorkflowType.T6_DIAGNOSTICS.value != "wf1"
    assert WorkflowType.T6_DIAGNOSTICS in WorkflowType
    assert registry.get_definition(WorkflowType.T6_DIAGNOSTICS) is definition
    assert registry.get_definition(WorkflowType.T6_DIAGNOSTICS.value) is definition
    assert isinstance(
        registry.get_workflow(WorkflowType.T6_DIAGNOSTICS.value),
        DiagnosticsWorkflow,
    )
    assert registry.list_workflow_ids() == ["wf1"]
