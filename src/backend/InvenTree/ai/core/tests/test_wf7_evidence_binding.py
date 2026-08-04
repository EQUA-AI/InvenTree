"""WF7 evidence binding (execution-plan S9).

The repair-packet workflow must run the Luna adapter with the diagnostic tool
registry and carry the model-declared confidence and evidence into the packet.
It must refuse — never invent — when there is no principal, no diagnostic
authority, or no overlap between the named generation target and the actor's
authorized record roots.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.reasoning.luna_diagnostics import ReasoningProvenance  # noqa: E402
from ai.core.reasoning.schemas import (  # noqa: E402
    CanonicalTurnResponse,
    EvidenceEntry,
    EvidenceLocator,
)
from ai.core.workflows.wf7_repair_packet import (  # noqa: E402
    RepairGenerationRefused,
    WF7RepairPacketWorkflow,
)
from django.test import SimpleTestCase  # noqa: E402

AS_OF = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="operator",
        authentication_method="in_process_generation",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _evidence() -> EvidenceEntry:
    return EvidenceEntry(
        source_type="machine",
        source_id="44",
        source_revision="r7",
        locator=EvidenceLocator(field="bearing_temp_c"),
        as_of=AS_OF,
        authorization_class="maintenance_scope",
        claim="Bearing temperature trended 18C above baseline over 3 days.",
    )


def _response(*, evidence: list[EvidenceEntry], state: str = "complete") -> CanonicalTurnResponse:
    return CanonicalTurnResponse(
        kind="diagnosis",
        response_version=1,
        response_state=state,
        detailed_response="Probable outboard bearing wear on the drive end.",
        spoken_summary="",
        reasoning_summary="Temperature trend plus vibration signature.",
        confidence="high",
        evidence=evidence,
        next_questions=["Capture a vibration spectrum at the drive-end bearing."],
        recommended_actions=[],
        safety_boundary="Advisory only; no physical work is authorized by this.",
        speak=False,
    )


def _provenance() -> ReasoningProvenance:
    return ReasoningProvenance(
        invocation_mode="direct_deployment",
        provider_request_id="req-1",
        effort="medium",
        deployment="luna-diagnosis-2",
    )


@dataclass
class _StubAdapter:
    response: CanonicalTurnResponse
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def reason(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(response=self.response, provenance=_provenance())


def _registry() -> Any:
    return SimpleNamespace(
        definitions=(
            SimpleNamespace(name="machine_overview", capability="diagnostics.machine.read"),
            SimpleNamespace(name="safety_p0", capability="diagnostics.safety_p0.read"),
        )
    )


def _roots() -> tuple[Any, ...]:
    return (
        SimpleNamespace(
            entity_type="machine",
            entity_id=44,
            expected_revision="r7",
            linked_machine_id=None,
            display_name="Influent Pump 1",
        ),
        SimpleNamespace(
            entity_type="repair_packet",
            entity_id=9,
            expected_revision="r2",
            linked_machine_id=44,
            display_name="RP-9",
        ),
        SimpleNamespace(
            entity_type="machine",
            entity_id=77,
            expected_revision="r1",
            linked_machine_id=None,
            display_name="Other Pump",
        ),
    )


def _context_factory(context: Any):
    async def factory(**kwargs: Any) -> Any:  # noqa: RUF029
        return context

    return factory


def _workflow(adapter: Any, context: Any) -> WF7RepairPacketWorkflow:
    return WF7RepairPacketWorkflow(
        reasoning_adapter=adapter,
        tool_registry=_registry(),
        diagnostic_context_factory=_context_factory(context),
    )


def _diag_context() -> Any:
    return SimpleNamespace(
        capabilities=("diagnostics.machine.read",),
        record_roots=_roots(),
    )


def _run(workflow: WF7RepairPacketWorkflow, *, principal: AIPrincipal | None, context: dict):
    async def exercise():
        return await workflow.execute(
            "Generate a repair packet for the seized pump",
            thread_id="repair-9:u7",
            context=context,
        )

    token = principal_context.set(principal)
    try:
        return asyncio.run(exercise())
    finally:
        principal_context.reset(token)


class WF7EvidenceBindingTests(SimpleTestCase):
    """The packet carries model-declared confidence and real citations."""

    def test_refuses_without_a_principal(self) -> None:
        adapter = _StubAdapter(_response(evidence=[_evidence()]))
        workflow = _workflow(adapter, _diag_context())
        with self.assertRaises(RepairGenerationRefused):
            _run(workflow, principal=None, context={})
        self.assertEqual(adapter.calls, [])

    def test_refuses_without_diagnostic_authority(self) -> None:
        adapter = _StubAdapter(_response(evidence=[_evidence()]))
        workflow = _workflow(adapter, None)
        with self.assertRaises(RepairGenerationRefused):
            _run(workflow, principal=_principal(), context={})
        self.assertEqual(adapter.calls, [])

    def test_refuses_an_unauthorized_generation_target(self) -> None:
        adapter = _StubAdapter(_response(evidence=[_evidence()]))
        workflow = _workflow(adapter, _diag_context())
        with self.assertRaises(RepairGenerationRefused):
            _run(
                workflow,
                principal=_principal(),
                context={"server_generation_target": {"machine_id": 999}},
            )
        self.assertEqual(adapter.calls, [])

    def test_evidence_and_declared_confidence_reach_the_packet(self) -> None:
        adapter = _StubAdapter(_response(evidence=[_evidence()]))
        workflow = _workflow(adapter, _diag_context())
        result = _run(
            workflow,
            principal=_principal(),
            context={
                "server_generation_target": {"machine_id": 44, "repair_packet_id": 9},
                "server_policy_key": "site:main",
                "policy_version": "1",
                "correlation_id": "00000000-0000-0000-0000-000000000009",
                "agent_run_id": "run-9",
            },
        )

        envelope = adapter.calls[0]["envelope"]
        self.assertEqual(envelope.machine_id, 44)
        self.assertEqual(envelope.repair_packet_id, 9)
        self.assertEqual(envelope.allowed_tool_names, ("machine_overview",))
        # The unauthorized-for-this-target root (machine 77) is not offered.
        self.assertEqual(
            [(r.entity_type, r.entity_id) for r in envelope.authorized_records],
            [("machine", 44), ("repair_packet", 9)],
        )

        diagnosis = result.diagnosis
        self.assertEqual(diagnosis["generator"], "wf7")
        self.assertEqual(diagnosis["status"], "available")
        self.assertEqual(diagnosis["confidence_label"], "high")
        # Band floor, not an invented point estimate.
        self.assertEqual(diagnosis["confidence"], 0.8)
        self.assertEqual(result.confidence, 0.8)
        citation = diagnosis["evidence"][0]
        self.assertEqual(citation["snapshot_id"], "machine:44@r7")
        self.assertEqual(citation["signal_label"], "bearing_temp_c")
        self.assertEqual(citation["relation"], "supports")
        self.assertEqual(result.agent_run_id, "run-9")

        # The payload must survive the fenced-JSON transport round-trip.
        match = re.search(r"```json\s*(\{.*\})\s*```", result.formatted_response, re.DOTALL)
        self.assertIsNotNone(match)
        parsed = json.loads(match.group(1))
        self.assertEqual(parsed["diagnosis"]["evidence"][0]["snapshot_id"], "machine:44@r7")

    def test_uncited_ai_diagnosis_is_insufficient(self) -> None:
        adapter = _StubAdapter(_response(evidence=[]))
        workflow = _workflow(adapter, _diag_context())
        result = _run(
            workflow,
            principal=_principal(),
            context={"server_generation_target": {"machine_id": 44}},
        )
        self.assertEqual(result.diagnosis["status"], "insufficient")
        self.assertEqual(result.diagnosis["confidence"], 0.0)
        self.assertEqual(result.diagnosis["confidence_label"], "unknown")
        self.assertEqual(result.confidence, 0.0)

    def test_non_complete_outcome_is_refused(self) -> None:
        incomplete = CanonicalTurnResponse(
            kind="diagnosis",
            response_version=1,
            response_state="incomplete",
            detailed_response="Insufficient authorized evidence to diagnose.",
            spoken_summary="",
            reasoning_summary="Abstained.",
            confidence="low",
            evidence=[],
            next_questions=[],
            recommended_actions=[],
            safety_boundary="Advisory only.",
            speak=False,
        )
        adapter = _StubAdapter(incomplete)
        workflow = _workflow(adapter, _diag_context())
        with self.assertRaises(RepairGenerationRefused):
            _run(
                workflow,
                principal=_principal(),
                context={"server_generation_target": {"machine_id": 44}},
            )

    def test_interactive_turn_without_target_offers_all_roots(self) -> None:
        adapter = _StubAdapter(_response(evidence=[_evidence()]))
        workflow = _workflow(adapter, _diag_context())
        result = _run(workflow, principal=_principal(), context={})
        envelope = adapter.calls[0]["envelope"]
        self.assertIsNone(envelope.machine_id)
        self.assertIsNone(envelope.repair_packet_id)
        self.assertEqual(len(envelope.authorized_records), 3)
        self.assertEqual(result.diagnosis["status"], "available")
