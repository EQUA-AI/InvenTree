"""Typed AI-generation boundary for Repair Packets (the ``wf7`` seam).

Architecture note (revised 2026-08-04, execution-plan S8): the AIMMS agent stack
(``ai/core``) is mounted in this same process (``InvenTree.asgi``), so repair
generation is an **in-process call through NormalizedTurnService** — the same
authenticated, idempotent, audited path interactive chat uses. The previous
standalone-service ``httpx.post`` forward path carried no credentials, was
always rejected by the AI boundary (401), and silently degraded every ``auto``
generation to the heuristic.

This module defines the generation boundary as a small, typed contract with two
providers:

* :class:`HeuristicGenerator` - a dependency-free, deterministic fallback that
  always produces a schema-conformant diagnosis. Guarantees the fault-to-fix loop
  works (and is testable) even when the AI stack is down or unconfigured.
* :class:`InProcessTurnGenerator` - the forward path. It runs ``wf7`` through
  ``NormalizedTurnService`` **as the requesting user**: no service identity
  exists, and a missing or inactive user means no AI generation at all.

``get_generator('auto')`` prefers the AI path and transparently falls back to
the heuristic provider on any error, so callers never fail closed on generation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
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
    # Provenance of the in-process turn that produced this result (empty for
    # the heuristic provider). Recorded on the generation run so the packet
    # UI can link back to the underlying AI turn.
    thread_id: str = ''
    turn_id: str = ''


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
def get_generator_mode() -> str:
    """Selected provider mode: ``auto`` | ``ai_service`` | ``heuristic``."""
    return os.environ.get('AIMMS_REPAIR_GENERATOR', 'auto').strip().lower()


def get_ai_timeout() -> float:
    """Wall-clock budget (seconds) for one in-process generation turn.

    Provider SDK retry loops are unbounded from this module's point of view;
    without a budget an unreachable model endpoint stalls generation for the
    provider's full retry schedule instead of falling back to the heuristic.
    """
    try:
        return float(os.environ.get('AIMMS_AI_TIMEOUT', '60'))
    except (TypeError, ValueError):
        return 60.0


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
# In-process (forward-path) provider
# --------------------------------------------------------------------------- #
class AIServiceUnavailableError(RuntimeError):
    """Raised when AI generation cannot run or returns no usable data."""


#: One turn service per Django process. Deliberately built from
#: ``get_root_workflow`` directly - importing ``ai.core.app`` would drag the
#: whole FastAPI surface (and its middleware) into every Django worker.
_turn_service: Any | None = None


def _get_turn_service() -> Any:
    global _turn_service
    if _turn_service is None:
        from ai.core.turn_service import NormalizedTurnService
        from ai.core.workflows.root import get_root_workflow

        _turn_service = NormalizedTurnService(workflow_factory=get_root_workflow)
    return _turn_service


def _extract_repair_payload(message: str | None) -> dict[str, Any] | None:
    """Pull the fenced ```json repair_packet payload out of a turn message."""
    if not isinstance(message, str) or not message:
        return None
    match = re.search(r'```json\s*(\{.*?\})\s*```', message, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and 'diagnosis' in parsed:
        return parsed.get('repair_packet', parsed)
    return None


def _payload_to_result(data: dict[str, Any], *, provider: str) -> GenerationResult:
    """Convert a ``repair_packet`` payload dict into a GenerationResult."""
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
        provider=provider,
    )


class InProcessTurnGenerator:
    """Runs ``wf7`` through the in-process ``NormalizedTurnService``.

    The turn executes **as the requesting user**: the principal is derived from
    ``context['user_id']`` via :func:`ai.core.auth.principal_for_user`. There is
    no service identity and no fallback actor - a missing or inactive user
    raises :class:`AIServiceUnavailableError` so ``auto`` mode degrades to the
    heuristic provider. The fallback is *no generation*, never a synthetic
    actor.
    """

    name = 'ai_service'

    def generate(
        self, *, fault_summary: str, context: dict[str, Any]
    ) -> GenerationResult:
        """Run one idempotent wf7 turn and parse its repair_packet payload."""
        principal = self._resolve_principal(context)

        packet_pk = context.get('repair_packet_id')
        run_id = str(context.get('agent_run_id') or '') or uuid.uuid4().hex
        idempotency_key = f'repair-gen:{packet_pk}:{run_id}'

        try:
            from asgiref.sync import async_to_sync

            result = async_to_sync(self._run_turn)(
                principal, fault_summary, context, idempotency_key
            )
        except AIServiceUnavailableError:
            raise
        except Exception as exc:
            logger.warning('in_process_generate_failed', error=str(exc))
            raise AIServiceUnavailableError(str(exc)) from exc

        structured = _extract_repair_payload(getattr(result, 'message', None))
        if not structured:
            raise AIServiceUnavailableError(
                'AI turn contained no repair_packet payload'
            )

        generation = _payload_to_result(structured, provider=self.name)
        generation.thread_id = str(getattr(result, 'thread_id', '') or '')
        generation.turn_id = str(getattr(result, 'turn_id', '') or '')
        return generation

    @staticmethod
    def _resolve_principal(context: dict[str, Any]) -> Any:
        """Resolve the requesting user's boundary principal, or refuse."""
        user_id = context.get('user_id')
        if user_id in (None, ''):
            raise AIServiceUnavailableError(
                'in-process generation requires the requesting user'
            )
        try:
            from django.contrib.auth import get_user_model

            from ai.core.auth import principal_for_user
        except ImportError as exc:  # pragma: no cover - split deployments only
            raise AIServiceUnavailableError(str(exc)) from exc

        try:
            user = get_user_model().objects.filter(pk=user_id).first()
        except (TypeError, ValueError):
            user = None
        try:
            return principal_for_user(user)
        except ValueError as exc:
            raise AIServiceUnavailableError(str(exc)) from exc

    async def _run_turn(
        self,
        principal: Any,
        fault_summary: str,
        context: dict[str, Any],
        idempotency_key: str,
    ) -> Any:
        from ai.core.auth import principal_context
        from ai.core.trusted_context import build_trusted_turn_context

        correlation_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f'{principal.subject}:{idempotency_key}')
        )
        trusted = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            server_route_hints=('/repair/generate',),
        )
        # Threads are owner-scoped; suffixing the actor keeps a regeneration by
        # a second user from colliding with the first user's thread.
        packet_pk = context.get('repair_packet_id')
        base_thread = str(context.get('thread_id') or f'repair-{packet_pk}')
        thread_id = f'{base_thread}:u{principal.user_pk}'[:80]
        prompt = (
            'Generate a repair packet diagnosis, parts path and applicable '
            f'safety gates for the following fault:\n\n{fault_summary}'
        )

        token = principal_context.set(principal)
        try:
            return await asyncio.wait_for(
                _get_turn_service().process(
                    actor=principal,
                    thread_id=thread_id,
                    content=prompt,
                    modality='text',
                    trusted_context=trusted,
                    modality_metadata=None,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    server_pinned_workflow='wf7',
                ),
                timeout=get_ai_timeout(),
            )
        finally:
            principal_context.reset(token)


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #
class AutoGenerator:
    """Prefer the AI path; fall back to the heuristic provider on any error."""

    name = 'auto'

    def __init__(self) -> None:
        """Instantiate the in-process AI and heuristic providers."""
        self._ai = InProcessTurnGenerator()
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
        return InProcessTurnGenerator()
    if mode == 'heuristic':
        return HeuristicGenerator()
    return AutoGenerator()


__all__ = [
    'DIAGNOSIS_SCHEMA_VERSION',
    'AIServiceUnavailableError',
    'AutoGenerator',
    'GeneratedPartLine',
    'GeneratedSafetyGate',
    'GenerationResult',
    'HeuristicGenerator',
    'InProcessTurnGenerator',
    'RepairPacketGenerator',
    'get_generator',
    'get_generator_mode',
]
