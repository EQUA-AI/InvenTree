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
    #: A3: a status phrase to speak BEFORE the routed answer (an unrelated
    #: reply set a pending write aside). None when nothing was set aside.
    pre_speech_status: str | None = None
    question_resolution: QuestionResolution | None = None
    #: A focus turn (explicit switch / clarification) captured this turn's
    #: canonical. When set it supersedes routing, exactly like
    #: ``write_canonical``. None when the turn routes normally.
    focus_canonical: dict[str, Any] | None = None
    # Routing stage outputs.
    routing_content: str = ""
    diagnostic_context: Any = None
    route: Any = None
    # S1+ analysis rail: typed per-turn state (never smuggled via ``extras``).
    # ``analysis_scope`` is the immutable scope snapshot ``begin_turn`` bound
    # under the thread row lock — None on threads without typed scope. The
    # remaining fields are populated by the analysis stages as they land
    # (intent S3, plan/retrieval S5+, validation S10, safety S4).
    analysis_scope: dict[str, Any] | None = None
    #: The immutable conversation-focus snapshot ``begin_turn`` bound under
    #: the SAME row lock (``{"focus": <stored projection>, "version": int}``).
    #: None on threads without a focus. On replay it is the ORIGINAL stored
    #: snapshot — a later focus switch never rebinds a recorded utterance.
    focus_snapshot: dict[str, Any] | None = None
    #: True when THIS turn changed the durable focus (spoken switch or a
    #: confirmed focus-selection answer). The turn's echo above is then the
    #: PRE-change recorded binding — the client must refresh
    #: ``GET /threads/{id}/focus`` for current state.
    focus_changed: bool = False
    #: Set when the recorded focus target failed its per-turn
    #: re-authorization: its label/ids must not guide routing or reads.
    focus_unavailable: bool = False
    # P02/P33: the verified Work focus ``begin_turn`` stored with the turn
    # (``aichat.services.work_focus``) — None for an unfocused turn. Separate
    # from ``analysis_scope``: focus names the record a question is about, it
    # never narrows or widens what the actor may read.
    work_focus: dict[str, Any] | None = None
    task_intent: Any = None
    query_plan: Any = None
    retrieval_snapshot: Any = None
    # M1: the ContextBundle the builder produced for this turn (memoized by
    # ``NormalizedTurnService.build_context_bundle``; None until built).
    context_bundle: Any = None
    validation_result: Any = None
    safety_response: Any = None
    # Non-init bookkeeping placeholder (kept for future stages).
    extras: dict[str, Any] = field(default_factory=dict)
