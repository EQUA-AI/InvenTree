"""The mutable per-turn state carrier threaded through pipeline stages (S47).

``TurnRun`` is package-internal: stages receive ``(service, run)`` and
communicate ONLY through this object and their return values. The canonical
dict is deliberately NOT a field — it is the value flowing
execution → enrichment → persistence, exactly mirroring the pre-extraction
dataflow (including the live ``canonical["events"] = run.capture.events``
alias).

Not named ``TurnState`` — that name is the aichat lifecycle enum already
imported throughout this package's neighborhood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai.core.auth import AIPrincipal
    from ai.core.questions.resolution import QuestionResolution
    from ai.core.streaming import EventEmitter
    from ai.core.trusted_context import TrustedTurnContext
    from ai.core.turn.events import _EventCapture


@dataclass(slots=True)
class TurnRun:
    """Everything one normalized turn accumulates across pipeline stages."""

    # Request (set by intake; read-only by convention afterwards).
    actor: AIPrincipal
    trusted_context: TrustedTurnContext
    content: str
    modality: str
    correlation_id: str
    idempotency_key: str
    server_pinned_workflow: str | None
    server_generation_target: dict[str, int] | None
    trusted: dict[str, Any]
    metadata: dict[str, Any]
    # Durable bindings (intake).
    repository: Any
    thread: Any
    turn: Any
    replayed_canonical: dict[str, Any] | None = None
    # Execution scaffolding (bound by the orchestrator after the replay check).
    emitter: EventEmitter | None = None
    capture: _EventCapture | None = None
    turn_started: float = 0.0
    # Pending-resolution stage outputs.
    injection_canonical: dict[str, Any] | None = None
    write_canonical: dict[str, Any] | None = None
    question_resolution: QuestionResolution | None = None
    # Routing stage outputs.
    routing_content: str = ""
    diagnostic_context: Any = None
    route: Any = None
    # Non-init bookkeeping placeholder (kept for future stages).
    extras: dict[str, Any] = field(default_factory=dict)
