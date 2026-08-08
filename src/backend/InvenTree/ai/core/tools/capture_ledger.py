"""Per-turn capture of what tools actually returned (S27).

The grounding validator's whole premise is "check the answer against what the
server showed the model" — which requires knowing what the server showed the
model. This bounded ContextVar ledger is that record: the capability
invocation middleware appends every tool result after dispatch, a
``search_manuals``-specific extractor keeps the citation dicts, and a generic
harvester keeps the identifier-like strings any tool surfaced
(``observed_values``), so an identifier the model repeats can be told apart
from one it invented.

Fail-soft and bounded by construction: recording never raises, an unbound
ledger is a no-op, and both the capture count and the retained bytes are
capped — the ledger observes the turn, it must never be able to kill it.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_CAPTURES = 64
MAX_TOTAL_BYTES = 128 * 1024
_MAX_OBSERVED_VALUES = 500
_HARVEST_KEYS = frozenset({
    "id",
    "pk",
    "part_id",
    "machine_id",
    "ipn",
    "serial",
    "name",
    "part_name",
    "reference",
    "document_id",
    "revision",
    "chunk_id",
})
_MAX_HARVEST_DEPTH = 6


@dataclass
class ToolCaptureLedger:
    """Bounded per-turn record of tool results."""

    captures: list[dict[str, Any]] = field(default_factory=list)
    total_bytes: int = 0
    observed: set[str] = field(default_factory=set)

    def record(self, tool_id: str, result: Any) -> None:
        """Record one tool result; oversized or excess results are dropped."""
        if len(self.captures) >= MAX_CAPTURES:
            return
        try:
            serialized = json.dumps(result, default=str)
        except (TypeError, ValueError):
            return
        size = len(serialized.encode("utf-8"))
        if self.total_bytes + size > MAX_TOTAL_BYTES:
            return
        self.total_bytes += size
        payload = result if isinstance(result, dict) else {"value": result}
        capture: dict[str, Any] = {"tool_id": str(tool_id)}
        citations = _manuals_citations(payload)
        if citations:
            capture["citations"] = citations
        self.captures.append(capture)
        _harvest_observed(payload, self.observed, depth=0)

    def manuals_citations(self) -> list[dict[str, Any]]:
        """Every captured manuals citation dict, in call order."""
        found: list[dict[str, Any]] = []
        for capture in self.captures:
            found.extend(capture.get("citations", ()))
        return found

    def observed_values(self) -> frozenset[str]:
        """Identifier-like strings some tool actually returned this turn."""
        return frozenset(self.observed)


def _manuals_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the search_manuals citation dicts, shape-checked."""
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        return []
    citations = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        citation = chunk.get("citation")
        if isinstance(citation, dict) and citation.get("chunk_id"):
            citations.append({
                "document": str(citation.get("document") or ""),
                "document_id": str(citation.get("document_id") or ""),
                "revision": str(citation.get("revision") or ""),
                "section_path": str(citation.get("section_path") or ""),
                "chunk_id": str(citation.get("chunk_id") or ""),
            })
    return citations


def _harvest_observed(value: Any, into: set[str], *, depth: int) -> None:
    """Collect identifier-like strings from a tool payload, bounded."""
    if len(into) >= _MAX_OBSERVED_VALUES or depth > _MAX_HARVEST_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _HARVEST_KEYS and isinstance(item, (str, int)):
                text = str(item).strip()
                if text and len(text) <= 128:
                    into.add(text)
            else:
                _harvest_observed(item, into, depth=depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _harvest_observed(item, into, depth=depth + 1)


tool_capture_ledger: ContextVar[ToolCaptureLedger | None] = ContextVar(
    "aimms_tool_capture_ledger", default=None
)


def bind_tool_captures() -> ToolCaptureLedger:
    """Bind a fresh ledger for this turn and return it.

    Rebinding (not set/reset) guarantees no cross-turn leakage even when a
    turn exits early — the next turn always starts empty.
    """
    ledger = ToolCaptureLedger()
    tool_capture_ledger.set(ledger)
    return ledger


def record_tool_result(tool_id: str, result: Any) -> None:
    """Record into the bound ledger; a no-op when unbound. Never raises."""
    try:
        ledger = tool_capture_ledger.get()
        if ledger is not None:
            ledger.record(tool_id, result)
    except Exception:  # pragma: no cover - observation must never kill a turn
        logger.debug("tool capture failed", exc_info=False)


def current_tool_captures() -> ToolCaptureLedger | None:
    """The bound ledger, or None."""
    try:
        return tool_capture_ledger.get()
    except Exception:  # pragma: no cover
        return None


__all__ = [
    "MAX_CAPTURES",
    "MAX_TOTAL_BYTES",
    "ToolCaptureLedger",
    "bind_tool_captures",
    "current_tool_captures",
    "record_tool_result",
    "tool_capture_ledger",
]
