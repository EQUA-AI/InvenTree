"""The §7.4 retrieval envelope (S5, WP-A2).

Every retrieval surface returns the same outer metadata so downstream
consumers — the model, the validator (S10), evidence records, telemetry —
can reason about coverage without guessing. The battery failure this
repairs: every list tool called its truncated page length ``count``, so
"12 work orders" could mean "12 of 12" or "the first 12 of 400".

Two halves, strictly separated:

- the **model-visible** envelope (this module's ``RetrievalEnvelope``),
  attached under a top-level ``"retrieval"`` key of the tool result;
- the **internal** meta (``internal_meta``) — the authorization scope hash
  and raw client codes — which never enters a model payload: it is recorded
  into the tool-capture ledger (``record_retrieval_meta``) for evidence and
  telemetry (A15).

Coverage vocabulary (mandatory distinction, §7.4): ``complete_population``
says whether the query EVALUATED every matching row (a server-side count
does, a 25-row page of 400 does not); ``display_truncated`` says whether
fewer rows were RETURNED than evaluated. A 25-row page from a 403-row
history has ``complete_population=False`` and cannot support absence,
rankings, or fleet rates.

Source-state vocabulary (A11): ``registered / attached / indexed /
applicable / searchable_now / current`` — and a zero-hit semantic search
says ``no_relevant_passage_retrieved`` (the strongest absence statement),
never "the document does not contain it".
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

#: The A11 source-state keys, in emission order.
SOURCE_STATE_KEYS: tuple[str, ...] = (
    "registered",
    "attached",
    "indexed",
    "applicable",
    "searchable_now",
    "current",
)

#: The strongest absence statement a zero-hit semantic search may make.
NO_RELEVANT_PASSAGE = "no_relevant_passage_retrieved"


class RetrievalEnvelope(TypedDict, total=False):
    """Model-visible retrieval metadata (§7.4)."""

    retrieval_id: str
    snapshot_id: str | None
    source_class: str
    population_type: str
    operation: str
    filters: dict[str, Any]
    coverage: dict[str, Any]
    source_revision: dict[str, Any]
    source_state: dict[str, bool]
    warnings: list[str]


def coverage(
    *,
    population_count: int,
    returned_count: int,
    complete_population: bool,
    display_truncated: bool | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Build the coverage block; ``display_truncated`` derives when omitted."""
    if display_truncated is None:
        display_truncated = returned_count < population_count
    return {
        "population_count": int(population_count),
        "returned_count": int(returned_count),
        "complete_population": bool(complete_population),
        "display_truncated": bool(display_truncated),
        "cursor": cursor,
    }


def build_envelope(
    *,
    source_class: str,
    population_type: str,
    operation: str,
    filters: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    source_revision: dict[str, Any] | None = None,
    source_state: dict[str, bool] | None = None,
    warnings: tuple[str, ...] | list[str] = (),
) -> RetrievalEnvelope:
    """Assemble one model-visible envelope with a fresh retrieval id.

    ``snapshot_id`` is filled from the bound turn-scope context when one
    exists (turn-stable across every envelope in the turn); tool wrappers
    never pass it explicitly.
    """
    from ai.core.analysis.scope_context import current_turn_scope

    scope = current_turn_scope()
    envelope: RetrievalEnvelope = {
        "retrieval_id": f"ret_{uuid.uuid4().hex[:12]}",
        "snapshot_id": scope.snapshot_id if scope is not None else None,
        "source_class": str(source_class),
        "population_type": str(population_type),
        "operation": str(operation),
        "filters": dict(filters or {}),
        "coverage": dict(coverage or {}),
        "warnings": list(warnings),
    }
    if source_revision:
        envelope["source_revision"] = dict(source_revision)
    if source_state:
        envelope["source_state"] = {
            key: bool(source_state.get(key, False)) for key in SOURCE_STATE_KEYS
        }
    return envelope


def record_envelope(tool_id: str, envelope: RetrievalEnvelope, **internal: Any) -> None:
    """Record the envelope's INTERNAL half into the capture ledger.

    ``internal`` carries the server-only coordinates (``authorization_scope_hash``,
    ``client_codes``, per-search counts) that must never transit the model.
    Fail-soft by construction (the ledger's own discipline).
    """
    from ai.core.analysis.scope_context import current_turn_scope
    from ai.core.tools.capture_ledger import record_retrieval_meta

    scope = current_turn_scope()
    meta: dict[str, Any] = {
        "retrieval_id": envelope.get("retrieval_id"),
        "snapshot_id": envelope.get("snapshot_id"),
        "source_class": envelope.get("source_class"),
        "operation": envelope.get("operation"),
        "coverage": envelope.get("coverage"),
    }
    if scope is not None:
        meta["authorization_scope_hash"] = scope.scope_hash
    meta.update(internal)
    record_retrieval_meta(tool_id, meta)


__all__ = [
    "NO_RELEVANT_PASSAGE",
    "SOURCE_STATE_KEYS",
    "RetrievalEnvelope",
    "build_envelope",
    "coverage",
    "record_envelope",
]
