"""§9.7 token estimation: tiktoken when it loads, a chars/4 floor otherwise.

Telemetry-only until calibrated (§9.8 ``estimator_error``); every section
budget is applied inside the char ceilings of ``ai/core/turn/history.py``,
so a wrong estimate can never blow a turn up.
"""

from __future__ import annotations

from typing import Any, Protocol


class TokenEstimator(Protocol):
    """One estimator; ``kind`` names it in telemetry (enum, never prose)."""

    kind: str

    def estimate(self, text: str) -> int: ...


class CharEstimator:
    """Deterministic fallback: ceil(len / 4)."""

    kind = "chars"

    def estimate(self, text: str) -> int:
        return (len(text) + 3) // 4


class TiktokenEstimator:
    """``o200k_base`` through the cached encoder in ``ai.core.usage``."""

    kind = "tiktoken"

    def __init__(self, encoder: Any):
        self._encoder = encoder

    def estimate(self, text: str) -> int:
        try:
            return len(self._encoder.encode(text))
        except Exception:
            return CharEstimator().estimate(text)


def default_estimator() -> TokenEstimator:
    """tiktoken when importable AND its vocabulary loads; else chars/4."""
    try:
        from ai.core.usage import _token_encoder

        encoder = _token_encoder()
    except Exception:
        encoder = None
    if encoder is None:
        return CharEstimator()
    return TiktokenEstimator(encoder)


__all__ = ["CharEstimator", "TiktokenEstimator", "TokenEstimator", "default_estimator"]
