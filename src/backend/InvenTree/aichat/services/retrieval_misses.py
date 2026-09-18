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
    corpus: str = 'governed',
    part_filter: str = '',
    scope_hash: str = '',
    scope_mode: str = '',
    scope_enforced: bool = False,
    out_of_scope_hits: int = 0,
) -> None:
    """Persist one search outcome; query metadata only, never answer text.

    ``corpus`` names which retrieval surface wrote the row (``governed`` for
    the controlled manuals, ``attachment`` for the R2 uploaded-document
    corpus, ``media`` for the R3 evidence-media corpus) so rollups stay
    separable; ``part_filter`` mirrors the attachment tool's part-narrowing
    outcome — for ``corpus='media'`` it carries the WORK-ORDER narrowing
    outcome instead (documented convention; a dedicated column is a deferred
    dark-safe migration). ``document_class`` carries ``media_type`` for media
    rows. All default to the pre-R2 shape so existing callers are untouched.

    The four ``scope_*`` fields are the S5 shadow evidence: which analysis
    scope was active, whether enforcement constrained the search, and how
    many candidate rows fell outside an explicit scope. Content-free — the
    hash is the thread scope's canonical digest, never machine names.
    """
    try:
        from django.contrib.auth import get_user_model
        from django.db import transaction

        from aichat.models import RetrievalMiss

        # Serialize owned query writes with account deactivation. In-flight
        # retrieval may finish later, but cannot repopulate erased query text.
        with transaction.atomic():
            if getattr(user, 'pk', None):
                user = (
                    get_user_model()
                    .objects.select_for_update()
                    .filter(pk=user.pk, is_active=True)
                    .first()
                )
                if user is None:
                    return
            _create_search_row(
                RetrievalMiss,
                user=user,
                query=query,
                hit_count=hit_count,
                top_score=top_score,
                machine_filter=machine_filter,
                document_class=document_class,
                scope_key=scope_key,
                corpus=corpus,
                part_filter=part_filter,
                scope_hash=scope_hash,
                scope_mode=scope_mode,
                scope_enforced=scope_enforced,
                out_of_scope_hits=out_of_scope_hits,
            )
    except Exception as exc:
        from ai.core.faults import fault_location

        logger.warning('retrieval-miss ledger write failed %s', fault_location(exc))


def _create_search_row(model, **values):
    # Keep the bounded projection together; no provider call occurs under lock.
    user, query = values['user'], values['query']
    hit_count, top_score = values['hit_count'], values['top_score']
    machine_filter, document_class = values['machine_filter'], values['document_class']
    scope_key, corpus = values['scope_key'], values['corpus']
    part_filter, scope_hash = values['part_filter'], values['scope_hash']
    scope_mode, scope_enforced = values['scope_mode'], values['scope_enforced']
    out_of_scope_hits = values['out_of_scope_hits']
    model.objects.create(
        user=user if getattr(user, 'pk', None) else None,
        query=str(query)[:_QUERY_MAX_LENGTH],
        hit_count=max(0, int(hit_count)),
        top_score=float(top_score) if top_score is not None else None,
        machine_filter=str(machine_filter or '')[:16],
        document_class=str(document_class or '')[:128],
        scope_key=str(scope_key or '')[:255],
        corpus=str(corpus or 'governed')[:32],
        part_filter=str(part_filter or '')[:16],
        scope_hash=str(scope_hash or '')[:64],
        scope_mode=str(scope_mode or '')[:32],
        scope_enforced=bool(scope_enforced),
        out_of_scope_hits=max(0, int(out_of_scope_hits)),
    )
