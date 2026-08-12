"""Per-turn provider usage ledger (S24, canonical vocabulary S37).

Both rails already measure usage — wf8 normalizes ``usage_details`` into a
log line and Luna reads ``response.usage`` for budget enforcement — and both
throw the numbers away. This ContextVar ledger collects them for the turn
and drains into terminal metadata, the same funnel ``model_versions`` uses.

S37 canonical keys: ``input_tokens``, ``output_tokens``,
``cached_input_tokens``, ``total_tokens``. Recording normalizes the two
vocabularies already in the wild (wf8/MAF's ``*_token_count`` and Luna's
Responses-API ``*_tokens``) into canonical form, and ``totals()`` sums ONLY
canonical keys — so cross-source totals merge into one number a budget can
compare. Non-canonical int keys (``history_messages`` …) stay as per-event
detail. Three named string fields survive per event: ``source``, ``model``,
``deployment``.

Known-uncounted sources (by name, so the gap is explicit): embeddings,
reflection repair calls, legacy workflows wf1-wf6, voice tool_actions, and
closeout extraction (it runs in the closeout wizard REST path, where no
turn ledger is bound). wf8 and the routing classifier record only on
success — failed provider calls are uncounted on those rails.

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

#: The canonical token vocabulary. ``totals()`` sums exactly these.
CANONICAL_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "total_tokens",
)

#: Vocabulary normalization applied at record time. Left side: keys emitted
#: by MAF ``usage_details`` (wf8, routing classifier) and provider cache
#: counters; right side: canonical.
_CANONICAL_RENAMES = {
    "input_token_count": "input_tokens",
    "output_token_count": "output_tokens",
    "total_token_count": "total_tokens",
    "cached_input_token_count": "cached_input_tokens",
    "cache_read_input_token_count": "cached_input_tokens",
    "cached_tokens": "cached_input_tokens",
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
}

#: The only non-integer fields an event may carry besides ``source``.
_ALLOWED_STRING_FIELDS = ("model", "deployment")


def _normalize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Map known vocabularies to canonical keys; keep ints + named strings."""
    cleaned: dict[str, Any] = {}
    for raw_key, value in (metrics or {}).items():
        key = _CANONICAL_RENAMES.get(raw_key, raw_key)
        if isinstance(value, int) and not isinstance(value, bool):
            # First writer wins on rename collisions (an explicit canonical
            # key outranks a renamed alias of itself).
            cleaned.setdefault(key, value)
        elif key in _ALLOWED_STRING_FIELDS and isinstance(value, str) and value:
            cleaned.setdefault(key, value[:64])
    return cleaned


@dataclass
class TurnUsageLedger:
    """Bounded per-turn accumulator of provider usage events."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, source: str, metrics: dict[str, Any]) -> None:
        """Append one normalized usage event; bounded."""
        if len(self.events) >= _MAX_EVENTS:
            return
        cleaned = _normalize_metrics(metrics)
        if any(isinstance(value, int) for value in cleaned.values()):
            self.events.append({"source": str(source), **cleaned})

    def totals(self) -> dict[str, int]:
        """Sum the canonical token keys across events."""
        totals: dict[str, int] = {}
        for event in self.events:
            for key in CANONICAL_TOKEN_KEYS:
                value = event.get(key)
                if isinstance(value, int):
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


def maf_response_usage_metrics(response: Any) -> dict[str, int]:
    """Extract usage from a MAF ``AgentRunResponse`` without inferring counts.

    Shared by wf8 and the routing classifier — both hold MAF responses whose
    usage lives on ``usage_details`` (never openai's ``.usage``). Emits the
    MAF vocabulary; ``record()`` normalizes it to canonical keys.
    """
    usage = getattr(response, "usage_details", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        counts = dict(usage)
    elif hasattr(usage, "to_dict"):
        counts = usage.to_dict(exclude_none=True)
    else:
        counts = {
            key: getattr(usage, key, None)
            for key in (
                "input_token_count",
                "output_token_count",
                "total_token_count",
            )
        }

    metrics = {
        key: value
        for key, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    cached = next(
        (
            metrics[key]
            for key in (
                "cache_read_input_token_count",
                "cached_input_token_count",
                "cached_tokens",
            )
            if key in metrics
        ),
        None,
    )
    input_tokens = metrics.get("input_token_count")
    if cached is not None:
        metrics["cached_input_token_count"] = cached
    if input_tokens is not None and cached is not None:
        metrics["uncached_input_token_count"] = max(input_tokens - cached, 0)
    return metrics


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
    "CANONICAL_TOKEN_KEYS",
    "TurnUsageLedger",
    "bind_turn_usage",
    "drain_turn_usage",
    "estimate_tokens",
    "maf_response_usage_metrics",
    "record_usage",
    "turn_usage_ledger",
]
