"""Fail-soft writer for the controlled-corpus retrieval ledger (S16 A7).

The ledger is telemetry, never a dependency: a failure to record must never
fail — or even slow — the search it observes, so every exception is swallowed
into one bounded fault log line.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Mirrors the search contract's own query bound (4000) capped to the column.
_QUERY_MAX_LENGTH = 500


def record_search(
    *,
    user,
    query: str,
    hit_count: int,
    top_score: float | None,
    machine_filter: str,
    document_class: str | None,
    scope_key: str,
) -> None:
    """Persist one search outcome; query metadata only, never answer text."""
    try:
        from aichat.models import RetrievalMiss

        RetrievalMiss.objects.create(
            user=user if getattr(user, 'pk', None) else None,
            query=str(query)[:_QUERY_MAX_LENGTH],
            hit_count=max(0, int(hit_count)),
            top_score=float(top_score) if top_score is not None else None,
            machine_filter=str(machine_filter or '')[:16],
            document_class=str(document_class or '')[:128],
            scope_key=str(scope_key or '')[:255],
        )
    except Exception as exc:
        from ai.core.faults import fault_location

        logger.warning('retrieval-miss ledger write failed %s', fault_location(exc))
