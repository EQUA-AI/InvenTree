"""Typed AI-generation boundary for Repair Packets (the ``wf7`` seam).

Architecture note (validated 2026-07-03): the AIMMS agent stack (``ai/core``)
runs as a **separate FastAPI service** (default ``http://localhost:8080``) on a
different Python runtime, and calls *back* into InvenTree's REST API. Django must
therefore treat generation as a **network boundary**, not an in-process import.

This module defines that boundary as a small, typed contract with two providers:

* :class:`HeuristicGenerator` - a dependency-free, deterministic fallback that
  always produces a schema-conformant diagnosis. Guarantees the fault-to-fix loop
  works (and is testable) even when the AI service is down or unconfigured.
* :class:`AIServiceGenerator` - the forward path that calls the AI service's
  ``/chat`` endpoint (which routes to ``wf7_repair_packet``) over HTTP.

``get_generator('auto')`` prefers the AI service and transparently falls back to
the heuristic provider on any error, so callers never fail closed on generation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from .schema import DIAGNOSIS_SCHEMA_VERSION, coerce_diagnosis, confidence_label

logger = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Result contract
# --------------------------------------------------------------------------- #
@dataclass
class GeneratedPartLine:
    """A part the repair is expected to need."""

    name: str
    part_id: int | None = None
    quantity: float = 1.0
    reason: str = ''


@dataclass
class GeneratedSafetyGate:
    """A safety gate the AI/heuristic deems applicable to this repair."""

    name: str
    gate_type: str = 'other'
    requires_photo: bool = False


@dataclass
class GenerationResult:
    """The complete, normalised output of a generation run."""

    diagnosis: dict[str, Any]
    parts: list[GeneratedPartLine] = field(default_factory=list)
    safety_gates: list[GeneratedSafetyGate] = field(default_factory=list)
    confidence: float = 0.0
    provider: str = 'unknown'


class RepairPacketGenerator(Protocol):
    """Protocol implemented by every generation provider."""

    name: str

    def generate(
        self, *, fault_summary: str, context: dict[str, Any]
    ) -> GenerationResult:
        """Produce a :class:`GenerationResult` for the given fault."""
        ...


# --------------------------------------------------------------------------- #
# Configuration (env-driven; no settings.py edits required)
# --------------------------------------------------------------------------- #
def get_ai_base_url() -> str:
    """Base URL of the AIMMS agent service."""
    return os.environ.get('AIMMS_AI_BASE_URL', 'http://localhost:8080').rstrip('/')


def get_ai_timeout() -> float:
    """HTTP timeout (seconds) for AI-service calls."""
    try:
        return float(os.environ.get('AIMMS_AI_TIMEOUT', '30'))
    except (TypeError, ValueError):
        return 30.0


def get_generator_mode() -> str:
    """Selected provider mode: ``auto`` | ``ai_service`` | ``heuristic``."""
    return os.environ.get('AIMMS_REPAIR_GENERATOR', 'auto').strip().lower()


# --------------------------------------------------------------------------- #
# Heuristic (offline) provider
# --------------------------------------------------------------------------- #
_ELECTRICAL_HINTS = (
    'motor',
    'contactor',
    'breaker',
    'coil',
    'voltage',
    'vfd',
    'wiring',
)
_ROTATING_HINTS = (
    'bearing',
    'pump',
    'fan',
    'shaft',
    'gearbox',
    'coupling',
    'vibration',
)


class HeuristicGenerator:
    """Deterministic, dependency-free generator used as the safe fallback."""

    name = 'heuristic'

    def generate(
        self, *, fault_summary: str, context: dict[str, Any]
    ) -> GenerationResult:
        """Derive a diagnosis, part lines and safety gates from keyword heuristics."""
        text = (fault_summary or '').strip()
        lowered = text.lower()

        first_sentence = re.split(r'(?<=[.!?])\s+', text)[0] if text else ''
        likely_cause = (
            f'Suspected fault related to: {first_sentence}'
            if first_sentence
            else 'Insufficient fault description to determine a likely cause.'
        )

        confirm_tests = ['Confirm the symptom is reproducible and capture readings.']
        gates: list[GeneratedSafetyGate] = []
        if any(h in lowered for h in _ELECTRICAL_HINTS):
            confirm_tests.append('Verify supply voltage / continuity at the device.')
            gates.append(
                GeneratedSafetyGate(
                    name='Lockout/Tagout (electrical isolation)',
                    gate_type='loto',
                    requires_photo=True,
                )
            )
        if any(h in lowered for h in _ROTATING_HINTS):
            confirm_tests.append(
                'Check alignment / lubrication and take vibration readings.'
            )
            gates.append(
                GeneratedSafetyGate(
                    name='Isolate and confirm zero rotational energy',
                    gate_type='isolation',
                    requires_photo=False,
                )
            )

        confidence = 0.3 if text else 0.0
        diagnosis = coerce_diagnosis({
            'likely_cause': likely_cause,
            'confidence': confidence,
            'confidence_label': confidence_label(confidence),
            'alternatives': [],
            'evidence': [],
            'confirm_tests': confirm_tests,
            'failure_mode': None,
            'generated_from': text,
            'generator': self.name,
        })
        return GenerationResult(
            diagnosis=diagnosis,
            parts=[],
            safety_gates=gates,
            confidence=confidence,
            provider=self.name,
        )


# --------------------------------------------------------------------------- #
# AI-service (forward-path) provider
# --------------------------------------------------------------------------- #
class AIServiceUnavailableError(RuntimeError):
    """Raised when the AI service cannot be reached or returns no usable data."""


class AIServiceGenerator:
    """Calls the AIMMS agent service (``wf7`` via ``/chat``) over HTTP.

    The AI service is expected to return a structured ``repair_packet`` payload
    embedded in its response (either as a top-level ``data`` object or a fenced
    JSON block in the assistant message). If no structured payload is present
    (e.g. ``wf7`` not yet deployed), this raises :class:`AIServiceUnavailableError`
    so ``auto`` mode can fall back to the heuristic provider.
    """

    name = 'ai_service'

    def generate(
        self, *, fault_summary: str, context: dict[str, Any]
    ) -> GenerationResult:
        """Request a structured repair packet from the AI service over HTTP."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx ships with InvenTree
            raise AIServiceUnavailableError('httpx is not installed') from exc

        url = f'{get_ai_base_url()}/chat'
        payload = {
            'message': (
                'Generate a repair packet diagnosis, parts path and applicable '
                f'safety gates for the following fault:\n\n{fault_summary}'
            ),
            'thread_id': context.get('thread_id'),
            'user_id': str(context.get('user_id') or 'repair-service'),
            'context': {**context, 'intent': 'repair_packet', 'workflow_hint': 'wf7'},
        }
        try:
            response = httpx.post(url, json=payload, timeout=get_ai_timeout())
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # network / decode / status errors
            logger.warning('ai_service_generate_failed', error=str(exc), url=url)
            raise AIServiceUnavailableError(str(exc)) from exc

        structured = self._extract_structured(body)
        if not structured:
            raise AIServiceUnavailableError(
                'AI response contained no repair_packet payload'
            )

        return self._to_result(structured)

    @staticmethod
    def _extract_structured(body: dict[str, Any]) -> dict[str, Any] | None:
        """Pull a repair_packet payload from a ChatResponse-shaped body."""
        if not isinstance(body, dict):
            return None
        for key in ('data', 'result', 'repair_packet'):
            value = body.get(key)
            if isinstance(value, dict) and (
                'diagnosis' in value or 'repair_packet' in value
            ):
                return value.get('repair_packet', value)
        # Fall back to a fenced ```json block inside the assistant message.
        message = body.get('message') or body.get('response') or ''
        if isinstance(message, str):
            match = re.search(r'```json\s*(\{.*?\})\s*```', message, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1))
                    if isinstance(parsed, dict) and 'diagnosis' in parsed:
                        return parsed
                except json.JSONDecodeError:
                    return None
        return None

    def _to_result(self, data: dict[str, Any]) -> GenerationResult:
        diagnosis = coerce_diagnosis(data.get('diagnosis', {}))
        parts = [
            GeneratedPartLine(
                name=str(p.get('name', '') or ''),
                part_id=p.get('part_id'),
                quantity=float(p.get('quantity', 1) or 1),
                reason=str(p.get('reason', '') or ''),
            )
            for p in data.get('parts', [])
            if isinstance(p, dict)
        ]
        gates = [
            GeneratedSafetyGate(
                name=str(g.get('name', '') or ''),
                gate_type=str(g.get('gate_type', 'other') or 'other'),
                requires_photo=bool(g.get('requires_photo', False)),
            )
            for g in data.get('safety_gates', [])
            if isinstance(g, dict) and g.get('name')
        ]
        return GenerationResult(
            diagnosis=diagnosis,
            parts=parts,
            safety_gates=gates,
            confidence=float(diagnosis.get('confidence', 0.0)),
            provider=self.name,
        )


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #
class AutoGenerator:
    """Prefer the AI service; fall back to the heuristic provider on any error."""

    name = 'auto'

    def __init__(self) -> None:
        """Instantiate the AI-service and heuristic providers."""
        self._ai = AIServiceGenerator()
        self._heuristic = HeuristicGenerator()

    def generate(
        self, *, fault_summary: str, context: dict[str, Any]
    ) -> GenerationResult:
        """Generate via the AI service, falling back to heuristics on any error."""
        try:
            return self._ai.generate(fault_summary=fault_summary, context=context)
        except Exception as exc:
            logger.info('generation_fallback_to_heuristic', reason=str(exc))
            return self._heuristic.generate(
                fault_summary=fault_summary, context=context
            )


def get_generator(mode: str | None = None) -> RepairPacketGenerator:
    """Return the configured generator provider."""
    mode = (mode or get_generator_mode()).strip().lower()
    if mode == 'ai_service':
        return AIServiceGenerator()
    if mode == 'heuristic':
        return HeuristicGenerator()
    return AutoGenerator()


__all__ = [
    'DIAGNOSIS_SCHEMA_VERSION',
    'AIServiceGenerator',
    'AIServiceUnavailableError',
    'AutoGenerator',
    'GeneratedPartLine',
    'GeneratedSafetyGate',
    'GenerationResult',
    'HeuristicGenerator',
    'RepairPacketGenerator',
    'get_generator',
    'get_generator_mode',
]
