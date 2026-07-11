"""
WF7: Repair Packet assembly workflow (the "spine" generator).

Composes existing workflows into the approval-ready Repair Packet payload that
the InvenTree ``repair`` app persists:

1. Run WF1 diagnostics on the fault text -> root causes, confidence, steps.
2. Map that into the versioned *diagnosis* schema the ``repair`` app expects.
3. Derive applicable *safety gates* (LOTO/isolation) from the fault signature.
4. (Forward work) resolve a *parts path* + *procurement* decision via WF2/WF4.

Contract (consumed by ``repair.generation.AIServiceGenerator``): ``invoke`` returns
a dict carrying a ``repair_packet`` object AND a fenced ```json block in the
``message`` field, so the Django side can extract the structured payload no matter
how the ``/chat`` endpoint wraps the workflow result.

NOTE: This runs inside the *AI service* runtime (separate Python 3.12+ with
``agent_framework``). It is syntax-validated but must be integration-tested in that
runtime; the Django side never hard-depends on it (it falls back to the heuristic
generator when the AI service is unavailable).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from ai.core.workflows.wf1_diagnostics import (
    DiagnosisConfidence,
    DiagnosticsResult,
    T6DiagnosticsWorkflow,
)

logger = logging.getLogger(__name__)

# Keep in sync with repair.schema.DIAGNOSIS_SCHEMA_VERSION on the InvenTree side.
DIAGNOSIS_SCHEMA_VERSION = 1

_CONFIDENCE_TO_SCORE = {
    DiagnosisConfidence.HIGH: 0.9,
    DiagnosisConfidence.MEDIUM: 0.65,
    DiagnosisConfidence.LOW: 0.3,
}

_ELECTRICAL_HINTS = ('motor', 'contactor', 'breaker', 'coil', 'voltage', 'vfd', 'wiring')
_ROTATING_HINTS = ('bearing', 'pump', 'fan', 'shaft', 'gearbox', 'coupling', 'vibration')


@dataclass
class RepairPacketResult:
    """Structured output of WF7 (mirrors ``repair.generation.GenerationResult``)."""

    diagnosis: dict[str, Any]
    parts_path: list[dict[str, Any]] = field(default_factory=list)
    procurement: dict[str, Any] | None = None
    safety_gates: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    agent_run_id: str = ''

    def as_payload(self) -> dict[str, Any]:
        """Return the ``repair_packet`` payload dict the repair app consumes."""
        return {
            'diagnosis': self.diagnosis,
            'parts': self.parts_path,
            'safety_gates': self.safety_gates,
            'procurement': self.procurement,
            'confidence': self.confidence,
            'agent_run_id': self.agent_run_id,
        }


class WF7RepairPacketWorkflow:
    """Assemble an approval-ready repair packet from a fault description."""

    def __init__(self, problem_solution_cache: Any | None = None) -> None:
        """Initialise, composing the WF1 diagnostics workflow."""
        self.diagnostics = T6DiagnosticsWorkflow(
            problem_solution_cache=problem_solution_cache
        )
        logger.info('WF7RepairPacketWorkflow initialized')

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _safety_gates_for(text: str) -> list[dict[str, Any]]:
        lowered = (text or '').lower()
        gates: list[dict[str, Any]] = []
        if any(h in lowered for h in _ELECTRICAL_HINTS):
            gates.append(
                {
                    'name': 'Lockout/Tagout (electrical isolation)',
                    'gate_type': 'loto',
                    'requires_photo': True,
                }
            )
        if any(h in lowered for h in _ROTATING_HINTS):
            gates.append(
                {
                    'name': 'Isolate and confirm zero rotational energy',
                    'gate_type': 'isolation',
                    'requires_photo': False,
                }
            )
        return gates

    @staticmethod
    def _diagnosis_from(result: DiagnosticsResult, fault: str) -> tuple[dict, float]:
        root_causes = list(getattr(result, 'root_causes', []) or [])
        top = root_causes[0] if root_causes else None
        likely_cause = getattr(top, 'description', '') or getattr(top, 'cause', '') \
            if top else ''
        if not likely_cause:
            likely_cause = f'Suspected fault related to: {fault}' if fault else ''

        confidence = 0.0
        conf_attr = getattr(top, 'confidence', None) if top else None
        if isinstance(conf_attr, DiagnosisConfidence):
            confidence = _CONFIDENCE_TO_SCORE.get(conf_attr, 0.3)
        elif isinstance(conf_attr, (int, float)):
            confidence = max(0.0, min(1.0, float(conf_attr)))

        alternatives = [
            getattr(rc, 'description', '') or getattr(rc, 'cause', '')
            for rc in root_causes[1:]
        ]
        confirm_tests = [
            getattr(step, 'description', '') or str(step)
            for step in (getattr(result, 'diagnosis_steps', []) or [])
        ] or ['Confirm the symptom is reproducible and capture readings.']

        label = (
            'high' if confidence >= 0.8
            else 'medium' if confidence >= 0.5
            else 'low' if confidence > 0 else 'unknown'
        )
        diagnosis = {
            'likely_cause': likely_cause,
            'confidence': confidence,
            'confidence_label': label,
            'alternatives': [a for a in alternatives if a],
            'evidence': [],
            'confirm_tests': confirm_tests,
            'failure_mode': None,
            'generator': 'wf7',
            'schema_version': DIAGNOSIS_SCHEMA_VERSION,
        }
        return diagnosis, confidence

    # -- public API ------------------------------------------------------- #
    async def execute(
        self,
        query: str,
        thread_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RepairPacketResult:
        """Run diagnostics and assemble a :class:`RepairPacketResult`."""
        run_id = (context or {}).get('agent_run_id') or uuid.uuid4().hex
        try:
            diag = await self.diagnostics.execute(query=query, thread_id=thread_id)
        except Exception as exc:  # degrade gracefully; the packet still forms
            logger.warning('wf7 diagnostics failed: %s', exc)
            diag = DiagnosticsResult()

        diagnosis, confidence = self._diagnosis_from(diag, query)
        return RepairPacketResult(
            diagnosis=diagnosis,
            parts_path=[],  # forward work: WF2 parts analysis + check_and_allocate
            procurement=None,  # forward work: WF4 procurement decision
            safety_gates=self._safety_gates_for(query),
            confidence=confidence,
            agent_run_id=run_id,
        )

    async def invoke(
        self,
        thread_id: str,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """WorkflowProtocol entry point returning a chat-forwardable dict."""
        result = await self.execute(user_message, thread_id=thread_id, context=context)
        payload = result.as_payload()
        return {
            'repair_packet': payload,
            'data': {'repair_packet': payload},
            'message': (
                'Repair packet diagnosis assembled.\n\n```json\n'
                + json.dumps(payload, indent=2)
                + '\n```'
            ),
        }

    def as_agent(self):
        """Expose the underlying diagnostics agent for nested composition."""
        return self.diagnostics.as_agent()
