"""Exceptions and typed shapes for the normalized turn pipeline (S47)."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class CanonicalTurn(TypedDict):
    """The canonical turn dict — typing only, the serialized shape is FROZEN.

    This types the SAME dict object the pipeline always built (a TypedDict
    adds no runtime behavior): ``_result_from_canonical`` is the reader,
    ``repository.terminal`` persists it verbatim, and idempotency replay
    re-emits its ``events`` byte-for-byte. The optional keys are added by
    later seams (arming, proposals, manifest, grounding) after construction.
    """

    thread_id: Any
    turn_id: Any
    message: str
    agent: str
    workflow_used: str | None
    response_state: Any
    canonical_response: dict[str, Any] | None
    spoken_summary: str
    reasoning_provenance: dict[str, Any] | None
    route: dict[str, Any] | None
    events: NotRequired[list[dict[str, Any]]]
    grounding: NotRequired[dict[str, Any]]
    entities: NotRequired[list[dict[str, Any]]]
    question: NotRequired[dict[str, Any]]
    question_resolution: NotRequired[dict[str, Any]]
    kind: NotRequired[str]


class TurnAlreadyRunning(RuntimeError):
    """Raised when an idempotency key refers to a non-terminal turn."""


class TurnExecutionFailed(RuntimeError):
    """Value-free public failure raised after a durable failed transition."""


class TurnIncomplete(RuntimeError):
    """Signal that bounded processing ended without a valid final answer."""
