"""
WF7: Repair Packet assembly workflow (the "spine" generator).

Produces the approval-ready Repair Packet payload the InvenTree ``repair`` app
persists. Since execution-plan S9 the diagnosis is **evidence-bound**: the
workflow runs the Luna reasoning adapter with the diagnostic tool registry —
the same bounded, re-authorizing tool loop the interactive diagnosis rail uses
— and carries the model-declared confidence and its evidence citations into
the packet. It never composes the tool-less wf1 path, and it never invents a
confidence score for prose the model did not ground.

Fail-closed contract: no authenticated principal, no diagnostic authority (no
capabilities or record roots), an unauthorized generation target, or a
non-complete provider outcome all raise — the Django side's ``auto`` mode then
falls back to the clearly-labelled offline heuristic instead of persisting an
ungrounded "AI" diagnosis.

Contract (consumed by ``repair.generation.InProcessTurnGenerator``): the result
carries a ``repair_packet`` payload AND renders it as a fenced ```json block in
the turn message (``formatted_response``), so the Django side can extract the
structured payload from the normalized turn result.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Keep in sync with repair.schema.DIAGNOSIS_SCHEMA_VERSION on the InvenTree side.
DIAGNOSIS_SCHEMA_VERSION = 2

#: The numeric floor of each declared confidence band. The repair schema stores
#: a 0..1 float and re-derives its label from fixed bands (>=0.8 high, >=0.5
#: medium, >0 low); encoding the model's declared *level* as that band's floor
#: round-trips the label exactly without inventing a point estimate.
_CONFIDENCE_BAND_FLOOR = {"low": 0.2, "medium": 0.5, "high": 0.8}

_ELECTRICAL_HINTS = ("motor", "contactor", "breaker", "coil", "voltage", "vfd", "wiring")
_ROTATING_HINTS = ("bearing", "pump", "fan", "shaft", "gearbox", "coupling", "vibration")


class RepairGenerationRefused(RuntimeError):
    """A grounded diagnosis cannot be produced; no packet content is invented."""


@dataclass
class RepairPacketResult:
    """Structured output of WF7 (mirrors ``repair.generation.GenerationResult``)."""

    diagnosis: dict[str, Any]
    parts_path: list[dict[str, Any]] = field(default_factory=list)
    procurement: dict[str, Any] | None = None
    safety_gates: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    agent_run_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        """Return the ``repair_packet`` payload dict the repair app consumes."""
        return {
            "diagnosis": self.diagnosis,
            "parts": self.parts_path,
            "safety_gates": self.safety_gates,
            "procurement": self.procurement,
            "confidence": self.confidence,
            "agent_run_id": self.agent_run_id,
        }

    @property
    def formatted_response(self) -> str:
        """Chat-forwardable message carrying the payload as a fenced block.

        RootWorkflow's execute-fallback yields this string as the turn message;
        ``repair.generation`` parses the fenced JSON back out of it. Without
        this property the dataclass repr would be streamed instead and the
        payload would be unrecoverable.
        """
        return (
            "Repair packet diagnosis assembled.\n\n```json\n"
            + json.dumps(self.as_payload(), indent=2)
            + "\n```"
        )


class WF7RepairPacketWorkflow:
    """Assemble an approval-ready repair packet from a fault description."""

    def __init__(
        self,
        reasoning_adapter: Any | None = None,
        tool_registry: Any | None = None,
        diagnostic_context_factory: Any | None = None,
    ) -> None:
        """Store injected seams; production wiring is built lazily on first use.

        Lazy construction keeps registry initialisation free of Azure imports:
        the adapter and registry only exist once a repair generation actually
        runs.
        """
        self._reasoning_adapter = reasoning_adapter
        self._tool_registry = tool_registry
        self._diagnostic_context_factory = diagnostic_context_factory
        logger.info("WF7RepairPacketWorkflow initialized")

    # -- wiring ----------------------------------------------------------- #
    def _ensure_reasoning(self) -> tuple[Any, Any]:
        """Build (adapter, registry) from settings unless seams were injected."""
        if self._reasoning_adapter is not None and self._tool_registry is not None:
            return self._reasoning_adapter, self._tool_registry

        from ai.core.config import get_settings
        from ai.core.reasoning.luna_diagnostics import LunaDiagnosticsAdapter
        from ai.core.tools.diagnostics import get_diagnostic_tool_registry

        configured = get_settings()
        registry = self._tool_registry or get_diagnostic_tool_registry(
            safety_p0_enabled=configured.repair_safety_p0s_closed,
            max_result_bytes=min(
                configured.azure_luna_diagnosis_max_tool_data_kb * 1024,
                64 * 1024,
            ),
        )
        adapter = self._reasoning_adapter or LunaDiagnosticsAdapter(tool_registry=registry)
        self._reasoning_adapter, self._tool_registry = adapter, registry
        return adapter, registry

    async def _diagnostic_context(self, principal: Any, query: str) -> Any | None:
        factory = self._diagnostic_context_factory
        if factory is None:
            from ai.core.reasoning.diagnostic_context import build_voice_diagnostic_context

            factory = build_voice_diagnostic_context
        return await factory(
            actor=principal,
            trusted_context=None,
            content=query,
            modality="text",
        )

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _safety_gates_for(text: str) -> list[dict[str, Any]]:
        lowered = (text or "").lower()
        gates: list[dict[str, Any]] = []
        if any(h in lowered for h in _ELECTRICAL_HINTS):
            gates.append({
                "name": "Lockout/Tagout (electrical isolation)",
                "gate_type": "loto",
                "requires_photo": True,
            })
        if any(h in lowered for h in _ROTATING_HINTS):
            gates.append({
                "name": "Isolate and confirm zero rotational energy",
                "gate_type": "isolation",
                "requires_photo": False,
            })
        return gates

    @staticmethod
    def _allowed_tool_names(registry: Any, diagnostic_context: Any) -> tuple[str, ...]:
        """Mirror the reasoning rail's single tool-exposure source."""
        capabilities = set(getattr(diagnostic_context, "capabilities", ()))
        return tuple(
            str(definition.name)
            for definition in getattr(registry, "definitions", ())
            if getattr(definition, "capability", None) in capabilities
        )

    @staticmethod
    def _select_roots(
        roots: tuple[Any, ...], target: dict[str, Any] | None
    ) -> tuple[list[Any], int | None, int | None]:
        """Narrow authorized roots to the server-named generation target.

        The target is information, never a grant: a root survives only if the
        actor was already authorized for it. With no target (an interactive
        turn routed here), every authorized root is offered and the model
        matches the record by display name, exactly like the diagnosis rail.
        """
        machine_id = target.get("machine_id") if target else None
        packet_id = target.get("repair_packet_id") if target else None
        if not target:
            return list(roots), None, None

        selected: list[Any] = []
        for root in roots:
            entity_type = getattr(root, "entity_type", None)
            entity_id = getattr(root, "entity_id", None)
            if (
                entity_type == "machine"
                and machine_id is not None
                and int(entity_id) == int(machine_id)
            ) or (
                entity_type == "repair_packet"
                and packet_id is not None
                and int(entity_id) == int(packet_id)
            ):
                selected.append(root)
        return (
            selected,
            int(machine_id) if machine_id is not None else None,
            int(packet_id) if packet_id is not None else None,
        )

    @staticmethod
    def _evidence_citation(entry: Any) -> dict[str, Any]:
        """Map one canonical EvidenceEntry into the repair citation shape."""
        citation: dict[str, Any] = {
            "snapshot_id": (f"{entry.source_type}:{entry.source_id}@{entry.source_revision}"),
            "observation": entry.claim,
            # A canonical evidence entry exists because the model cited it in
            # support of the visible claim; contradicting observations surface
            # in the prose, not as citations.
            "relation": "supports",
            "observed_at": entry.as_of.isoformat(),
        }
        locator_field = getattr(getattr(entry, "locator", None), "field", None)
        if locator_field:
            citation["signal_label"] = locator_field
        return citation

    def _diagnosis_from_response(
        self, response: Any, provenance: Any
    ) -> tuple[dict[str, Any], float]:
        citations = [self._evidence_citation(entry) for entry in response.evidence]
        level = str(getattr(response.confidence, "value", response.confidence))
        confidence = _CONFIDENCE_BAND_FLOOR.get(level, 0.0)
        status = "available"
        if not citations:
            # An AI diagnosis with no authorized citations must not read as a
            # confident, available answer — the prose remains for the human,
            # the trust does not.
            confidence = 0.0
            level = "unknown"
            status = "insufficient"

        diagnosis = {
            "likely_cause": response.detailed_response,
            "confidence": confidence,
            "confidence_label": level,
            "alternatives": [],
            "evidence": citations,
            "confirm_tests": list(response.next_questions),
            "failure_mode": None,
            "status": status,
            "authority": "derived",
            "provider": "azure_foundry_luna",
            "model_or_rule_version": (
                getattr(provenance, "deployment", "") or getattr(provenance, "agent_name", "")
            ),
            "generated_at": datetime.now(UTC).isoformat(),
            "generator": "wf7",
            "reasoning_summary": response.reasoning_summary,
            "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        }
        return diagnosis, confidence

    # -- public API ------------------------------------------------------- #
    async def execute(
        self,
        query: str,
        thread_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RepairPacketResult:
        """Run one evidence-bound reasoning pass and assemble the packet."""
        from ai.core.auth import get_current_principal
        from ai.core.reasoning.luna_diagnostics import (
            AuthorizedRecord,
            TrustedReasoningEnvelope,
        )

        context = context or {}
        run_id = context.get("agent_run_id") or uuid.uuid4().hex

        principal = get_current_principal()
        if principal is None or getattr(principal, "user_pk", None) is None:
            raise RepairGenerationRefused("no authenticated principal for generation")

        diagnostic_context = await self._diagnostic_context(principal, query)
        adapter, registry = self._ensure_reasoning()
        allowed_tools = (
            self._allowed_tool_names(registry, diagnostic_context)
            if diagnostic_context is not None
            else ()
        )
        if diagnostic_context is None or not allowed_tools:
            raise RepairGenerationRefused("no authorized diagnostic tools in scope")

        target = context.get("server_generation_target")
        roots, machine_id, packet_id = self._select_roots(
            tuple(getattr(diagnostic_context, "record_roots", ())),
            target if isinstance(target, dict) else None,
        )
        if not roots:
            raise RepairGenerationRefused(
                "generation target is not among the actor's authorized records"
            )

        authorized_records = tuple(
            AuthorizedRecord(
                entity_type=root.entity_type,
                entity_id=int(root.entity_id),
                expected_revision=str(root.expected_revision),
                linked_machine_id=(
                    int(root.linked_machine_id)
                    if getattr(root, "linked_machine_id", None) is not None
                    else None
                ),
                display_name=str(getattr(root, "display_name", "") or ""),
            )
            for root in roots
        )

        # S36: prefer the turn's threaded id, then the bound context var; a
        # fresh mint is the last resort and is logged as a spine
        # discontinuity so silent re-mints stay visible.
        from ai.core.correlation import current_correlation

        correlation_id = str(context.get("correlation_id") or current_correlation() or "")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())
            logger.warning("wf7 minted a fresh correlation id (spine discontinuity)")

        envelope = TrustedReasoningEnvelope(
            actor_id=str(principal.actor),
            scope={"policy_key": str(context.get("server_policy_key") or principal.scope)},
            thread_id=str(thread_id or f"wf7-{run_id}")[:80],
            machine_id=machine_id,
            repair_packet_id=packet_id,
            user_message=query,
            mode="text",
            allowed_tool_names=allowed_tools,
            authorized_records=authorized_records,
            policy_version=str(context.get("policy_version") or principal.policy_version),
            correlation_id=correlation_id,
            # W0: server-derived locale so wf7's reasoning respects the
            # user's language exactly like the main reasoning rail.
            locale=str(context.get("locale") or "en"),
        )

        outcome = await adapter.reason(envelope=envelope, tool_context=diagnostic_context)
        response = outcome.response
        state = getattr(response.response_state, "value", response.response_state)
        if str(state) != "complete":
            # Value-free observability: live triage of auto-mode fallbacks needs
            # to distinguish an honest abstention/demotion from a provider
            # failure without exposing content (found 2026-08-06: the wrapped
            # "AI turn failed" left the outcome shape invisible).
            logger.info("wf7 reasoning outcome not complete (state=%s)", state)
            raise RepairGenerationRefused(f"reasoning outcome was {state}")

        diagnosis, confidence = self._diagnosis_from_response(response, outcome.provenance)
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
            "repair_packet": payload,
            "data": {"repair_packet": payload},
            "message": result.formatted_response,
        }
