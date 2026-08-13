"""Public control-flow exceptions for the normalized turn pipeline (S47)."""

from __future__ import annotations


class TurnAlreadyRunning(RuntimeError):
    """Raised when an idempotency key refers to a non-terminal turn."""


class TurnExecutionFailed(RuntimeError):
    """Value-free public failure raised after a durable failed transition."""


class TurnIncomplete(RuntimeError):
    """Signal that bounded processing ended without a valid final answer."""
