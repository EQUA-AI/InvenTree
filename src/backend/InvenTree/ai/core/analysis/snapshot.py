"""Snapshot integrity v1 for complete-population analytics (S7, §8.3.5).

One analysis turn answers from ONE consistent view of the data. The
guarantee is an operand-version hash: the ordered ``pk:version`` list of
every row the calculation evaluated, hashed before compute and rechecked
after validation. ``MAX(updated_at)`` alone cannot detect an insertion, a
deletion, or an amendment that leaves the watermark unchanged — the full
ordered list can. On divergence the executor retries once from scratch;
a second divergence returns the typed ``snapshot_changed`` incomplete,
never a synthesis over mixed states.

Deliberately deferred, with the named safeguard in force (owner-approved
design 2026-08-29):

- **True operand materialization** (a snapshot table / REPEATABLE READ):
  the post-validate recheck is the safeguard — a torn read is detected
  and retried, not served.
- **Azure Search internal revisions**: document pins are content-hash
  rows (``document_id:revision:sha256``); the manifest notes
  ``row_pinned_only`` when only row-level pins protect an answer.
- **Cross-request snapshot reuse**: every turn resolves fresh; the
  manifest records, it never caches.

Version-string vocabulary (must stay in lockstep with the operand scans
in ``tasks/ai_analytics.py``): work orders and maintenance records use
``pk:updated_at``; procedure applications ``pk:policy_version:applied_at``;
step executions ``pk:version``; controlled documents
``document_id:revision:source_sha256``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class AnalysisRetrievalIncomplete(Exception):
    """A typed retrieval outcome the executor renders as an incomplete.

    Raised by the analytics retrieval bodies when the answer is honestly
    unavailable in a NAMED way — a grouping outside the vocabulary, a
    bucket range past the cap, a population past the membership envelope,
    or a snapshot that would not hold still. ``code`` must be a member of
    the ``IncompleteReason`` wire vocabulary.
    """

    def __init__(self, code: str, message: str = "", *, facets: tuple[str, ...] = ()) -> None:
        """Store the wire code (and optionally the unmet facet names)."""
        super().__init__(message or code)
        self.code = code
        #: S9: a gate-unmet outcome names WHICH required facets were
        #: missing; the executor emits one IncompleteReason per name.
        self.facets = tuple(facets)


def operand_hash(rows: Iterable[tuple[Any, Any]]) -> str:
    """sha256 over the ordered ``pk:version`` operand list.

    The caller supplies rows in a stable order (the scans order by pk);
    hashing is line-oriented so a pk shift, a version bump, an insertion
    and a deletion all change the digest.
    """
    digest = hashlib.sha256()
    for pk, version in rows:
        digest.update(f"{pk}:{version}\n".encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotManifest:
    """What one analysis turn's answer was computed FROM (§7.3 record).

    Stored on ``TurnRun.query_plan`` (its first assignment ever) and, as
    ``operand_hash``, on the turn's evidence set — the expansion endpoint
    and any later audit can name the exact operand state.
    """

    snapshot_id: str
    operand_hash: str
    operand_count: int
    sources: dict[str, Any] = field(default_factory=dict)
    document_pins: tuple[dict[str, Any], ...] = ()
    as_of: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe projection for persistence and telemetry."""
        return {
            "snapshot_id": self.snapshot_id,
            "operand_hash": self.operand_hash,
            "operand_count": self.operand_count,
            "sources": dict(self.sources),
            "document_pins": [dict(pin) for pin in self.document_pins],
            "as_of": self.as_of,
            "plan": dict(self.plan),
            "notes": list(self.notes),
        }


def build_manifest(
    *,
    snapshot_id: str,
    operands: Iterable[tuple[Any, Any]],
    sources: Mapping[str, Any],
    plan: Mapping[str, Any],
    as_of: str,
    document_pins: Iterable[Mapping[str, Any]] = (),
    notes: Iterable[str] = (),
) -> SnapshotManifest:
    """Hash the operand list and assemble the manifest in one step."""
    rows = list(operands)
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        operand_hash=operand_hash(rows),
        operand_count=len(rows),
        sources=dict(sources),
        document_pins=tuple(dict(pin) for pin in document_pins),
        as_of=as_of,
        plan=dict(plan),
        notes=tuple(notes),
    )


__all__ = [
    "AnalysisRetrievalIncomplete",
    "SnapshotManifest",
    "build_manifest",
    "operand_hash",
]
