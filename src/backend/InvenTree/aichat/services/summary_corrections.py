"""User corrections to a thread's compaction summary (M2 PR 3, plan §8.7).

``forget_item`` is the owner's forget: the item is flipped to
``forgotten`` and its content-free fingerprint joins the body's
``exclusions`` so no later compaction can revive it (GR-03). The write is
compare-and-set on the summary text and watermark, and the
``thread_summary`` outbox kind (``retention.OUTBOX_KINDS``) re-applies the
exclusions afterwards so a lost race still converges.

Value-free discipline: log lines and results carry ids, enum codes and
counts — never the item text.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum

from ai.core.memory.summary_body import (
    ITEM_LISTS,
    active_items,
    apply_exclusions,
    excluded_keys,
    is_active,
    item_id,
    item_lifecycle,
    item_text,
    normalize_text,
    parse_summary,
    upgrade_body,
)
from ai.core.memory.vocabulary import FactLifecycle

logger = logging.getLogger('inventree')

#: Read-CAS-write attempts before a persistent race is reported.
_WRITE_ATTEMPTS = 3


class ForgetResult(StrEnum):
    """Outcome of ``forget_item`` (also the PR 5 endpoint's response code)."""

    APPLIED = 'applied'
    UNKNOWN_ITEM = 'unknown_item'
    THREAD_NOT_FOUND = 'thread_not_found'


class ForgetReason(StrEnum):
    """Why the owner removed the item — PR 5's action names."""

    FORGET = 'forget'
    WRONG = 'wrong'


class SummaryWriteRaceError(Exception):
    """The summary changed underneath every write attempt."""


def _store(label: str, body: dict) -> str:
    return label + '\n' + json.dumps(body, ensure_ascii=True)


def _read(thread_id: str, *, actor_pk: int | None = None):
    """``(summary, watermark)`` for the thread, or ``None`` when absent."""
    from aichat.models import ChatThread

    rows = ChatThread.objects.filter(pk=thread_id)
    if actor_pk is not None:
        rows = rows.filter(owner_id=actor_pk)
    return rows.values_list('summary', 'summary_through_sequence').first()


def _cas_write(thread_id: str, *, expected: str, watermark: int, new: str) -> bool:
    from aichat.models import ChatThread

    return bool(
        ChatThread.objects.filter(
            pk=thread_id, summary_through_sequence=watermark, summary=expected
        ).update(summary=new)
    )


def _find(body: dict, identifier: str) -> dict | None:
    for field in ITEM_LISTS:
        for item in body.get(field) or []:
            if item_id(item) == identifier:
                return item
    return None


# --------------------------------------------------------------------------- #
# GR-03 over the prose fields (plan §5.6 / §8.7: "protected lists AND the      #
# narrative")                                                                  #
# --------------------------------------------------------------------------- #
#: The prose fields a compaction stores verbatim from the model.
_PROSE_FIELDS = ('narrative', 'label')


def forgotten_texts(body: dict) -> list[str]:
    """Normalized texts of the body's forgotten items.

    A forgotten item keeps its ``text`` in the body while it survives the
    inactive history cap, so the revival check needs no content in
    ``exclusions`` (which stay fingerprints only). Covers every
    ``forgotten`` item and every non-active item an exclusion names by id
    or fingerprint. Pure.
    """
    ids, fingerprints = excluded_keys(body)
    texts: list[str] = []
    for field in ITEM_LISTS:
        for item in body.get(field) or []:
            if not isinstance(item, dict) or is_active(item):
                continue
            named = (
                item_id(item) in ids
                or str(item.get('fingerprint') or '') in fingerprints
            )
            if named or item_lifecycle(item) == str(FactLifecycle.FORGOTTEN):
                normalized = normalize_text(item_text(item))
                if normalized:
                    texts.append(normalized)
    return texts


def revives_forgotten_text(
    body: dict, text: str, *, texts: list[str] | None = None
) -> bool:
    """Whether free text restates a forgotten item.

    A token-bounded substring test over ``normalize_text`` output (the
    same normalization the fingerprint uses), so ``Restated: the motor is
    5.5 kW.`` matches the forgotten ``The motor is 5.5 kW`` while ``belt
    ok`` never matches ``the belt okay``. Pure.
    """
    normalized = normalize_text(text or '')
    if not normalized:
        return False
    haystack = f' {normalized} '
    needles = forgotten_texts(body) if texts is None else texts
    return any(f' {needle} ' in haystack for needle in needles)


def scrub_revived_prose(body: dict) -> tuple[dict, int]:
    """Blank ``narrative``/``label`` that restate a forgotten item.

    Mutates and returns ``(body, hits)``; each blanked field is one hit.
    """
    texts = forgotten_texts(body)
    hits = 0
    if not texts:
        return body, 0
    for key in _PROSE_FIELDS:
        if revives_forgotten_text(body, str(body.get(key) or ''), texts=texts):
            body[key] = ''
            hits += 1
    return body, hits


def forget_item(
    thread_id: str, item_id: str, *, actor_pk: int, reason: str
) -> ForgetResult:
    """Flip one summary item to ``forgotten`` and record its exclusion.

    The caller owner-checks; the service filters on the owner again
    (defense in depth). Idempotent: a second call for an item that is
    already non-active and excluded is ``APPLIED`` without a write. The
    watermark never moves. Up to three read-CAS-write attempts; a
    persistent race raises :class:`SummaryWriteRaceError`.

    Args:
        thread_id: The thread's primary key.
        item_id: The per-fact id (``mf3``, ``oq1``, ...).
        actor_pk: The acting user's primary key; must own the thread.
        reason: A ``ForgetReason`` value.

    Returns:
        The ``ForgetResult`` code.
    """
    reason_code = str(ForgetReason(reason))
    identifier = str(item_id or '').strip()
    for _attempt in range(_WRITE_ATTEMPTS):
        row = _read(thread_id, actor_pk=actor_pk)
        if row is None:
            return ForgetResult.THREAD_NOT_FOUND
        summary_read, watermark = row
        label, raw_body = parse_summary(summary_read)
        body = upgrade_body(raw_body, created_seq=int(watermark))
        item = _find(body, identifier)
        if item is None:
            return ForgetResult.UNKNOWN_ITEM
        ids, _ = excluded_keys(body)
        if item['lifecycle'] != str(FactLifecycle.ACTIVE) and identifier in ids:
            return ForgetResult.APPLIED
        item['lifecycle'] = str(FactLifecycle.FORGOTTEN)
        body['exclusions'].append({
            'fingerprint': item['fingerprint'],
            'item_id': identifier,
            'created_seq': int(watermark),
            'reason': reason_code,
        })
        # Same-fingerprint siblings (a restated fact) go with it, and so
        # does a label that restates the text; the narrative always goes.
        body, _hits = apply_exclusions(body)
        body, _prose_hits = scrub_revived_prose(body)
        body['narrative'] = ''
        if revives_forgotten_text(body, label):
            label = ''
        if _cas_write(
            thread_id,
            expected=summary_read,
            watermark=int(watermark),
            new=_store(label, body),
        ):
            from aichat.services.retention import enqueue_outbox

            enqueue_outbox('thread_summary', str(thread_id))
            logger.info(
                'Summary item forgotten thread=%s item=%s actor=%s reason=%s',
                thread_id,
                identifier,
                actor_pk,
                reason_code,
            )
            return ForgetResult.APPLIED
    raise SummaryWriteRaceError()


def reapply_exclusions(thread_id: str) -> int:
    """Re-apply the body's exclusions (the ``thread_summary`` outbox handler).

    Writes only when an active item is still named by an exclusion or a
    prose field (``narrative``, ``label``) restates a forgotten item.
    Returns the number of items flipped plus prose fields blanked; 0 means
    already clean. A missing thread is success (0) — a purged thread owes
    nothing.
    """
    for _attempt in range(_WRITE_ATTEMPTS):
        row = _read(thread_id)
        if row is None:
            return 0
        summary_read, watermark = row
        label, raw_body = parse_summary(summary_read)
        if not raw_body:
            return 0
        body = upgrade_body(raw_body, created_seq=int(watermark))
        body, hits = apply_exclusions(body)
        body, prose_hits = scrub_revived_prose(body)
        hits += prose_hits
        if revives_forgotten_text(body, label):
            label = ''
            hits += 1
        if not hits:
            return 0
        if _cas_write(
            thread_id,
            expected=summary_read,
            watermark=int(watermark),
            new=_store(label, body),
        ):
            logger.info(
                'Summary exclusions re-applied thread=%s hits=%d', thread_id, hits
            )
            return hits
    raise SummaryWriteRaceError()


def excluded_residual(thread_id: str) -> int:
    """The outbox residual probe: what a re-apply would still change.

    Active items an exclusion still names, plus prose fields (narrative,
    label) that restate a forgotten item.
    """
    row = _read(thread_id)
    if row is None:
        return 0
    label, raw_body = parse_summary(row[0])
    if not raw_body:
        return 0
    body = upgrade_body(raw_body, created_seq=int(row[1]))
    ids, fingerprints = excluded_keys(body)
    residual = sum(
        1
        for field in ITEM_LISTS
        for item in active_items(body, field)
        if item['id'] in ids or item['fingerprint'] in fingerprints
    )
    texts = forgotten_texts(body)
    if texts:
        # The first line is the label the readers show; it normally equals
        # ``body["label"]`` and is counted once when it does.
        prose = {str(body.get(key) or '') for key in _PROSE_FIELDS} | {label}
        residual += sum(
            1 for text in prose if revives_forgotten_text(body, text, texts=texts)
        )
    return residual


__all__ = [
    'ForgetReason',
    'ForgetResult',
    'SummaryWriteRaceError',
    'excluded_residual',
    'forget_item',
    'forgotten_texts',
    'reapply_exclusions',
    'revives_forgotten_text',
    'scrub_revived_prose',
]
