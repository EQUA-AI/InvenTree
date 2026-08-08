"""Per-turn provider usage ledger (S24).

Both rails already measure usage — wf8 normalizes ``usage_details`` into a
log line and Luna reads ``response.usage`` for budget enforcement — and both
throw the numbers away. This ContextVar ledger collects them for the turn
and drains into terminal metadata, the same funnel ``model_versions`` uses.

Fail-soft by construction: recording never raises, an unbound ledger is a
no-op, and the event list is bounded. Usage is telemetry — it must never be
able to fail or slow a turn.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_MAX_EVENTS = 32


@dataclass
class TurnUsageLedger:
    """Bounded per-turn accumulator of provider usage events."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, source: str, metrics: dict[str, Any]) -> None:
        """Append one usage event; integer metrics only, bounded."""
        if len(self.events) >= _MAX_EVENTS:
            return
        cleaned = {
            key: value
            for key, value in (metrics or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if cleaned:
            self.events.append({"source": str(source), **cleaned})

    def totals(self) -> dict[str, int]:
        """Sum every integer metric across events (source excluded)."""
        totals: dict[str, int] = {}
        for event in self.events:
            for key, value in event.items():
                if key == "source" or not isinstance(value, int):
                    continue
                totals[key] = totals.get(key, 0) + value
        return totals


turn_usage_ledger: ContextVar[TurnUsageLedger | None] = ContextVar(
    "aimms_turn_usage_ledger", default=None
)


@contextmanager
def bind_turn_usage():
    """Bind a fresh ledger for one turn; always unbinds."""
    ledger = TurnUsageLedger()
    token = turn_usage_ledger.set(ledger)
    try:
        yield ledger
    finally:
        turn_usage_ledger.reset(token)


def record_usage(source: str, metrics: dict[str, Any]) -> None:
    """Record usage into the bound ledger; a no-op when unbound."""
    try:
        ledger = turn_usage_ledger.get()
        if ledger is not None:
            ledger.record(source, metrics)
    except Exception:  # pragma: no cover - telemetry must never raise
        logger.debug("usage recording failed", exc_info=False)


def drain_turn_usage() -> dict[str, Any] | None:
    """Return the bound ledger's payload, or None when empty/unbound."""
    try:
        ledger = turn_usage_ledger.get()
        if ledger is None or not ledger.events:
            return None
        return {"events": list(ledger.events), "totals": ledger.totals()}
    except Exception:  # pragma: no cover - telemetry must never raise
        return None


@lru_cache(maxsize=1)
def _token_encoder() -> Any:
    """Import tiktoken's o200k_base encoder once; None when unavailable."""
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def estimate_tokens(text: str) -> int | None:
    """Best-effort token estimate for telemetry; None when unavailable.

    Never used for enforcement — budgets are char-based so behavior cannot
    depend on whether tiktoken is installed.
    """
    encoder = _token_encoder()
    if encoder is None:
        return None
    try:
        return len(encoder.encode(text))
    except Exception:
        return None


__all__ = [
    "TurnUsageLedger",
    "bind_turn_usage",
    "drain_turn_usage",
    "estimate_tokens",
    "record_usage",
    "turn_usage_ledger",
]
