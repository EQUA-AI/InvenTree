"""Signed, bounded thread-deletion journals for an isolated restored database.

The source must be quiescent when exporting. A journal proves the exported
records, not the completeness of the source's history or a whole-account erase.
"""

import hashlib
import os
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aichat.models import AIRetentionOutbox, ChatThread, ChatThreadTombstone
from aichat.services import retention
from InvenTree.restore_hold import restore_hold_enabled

SALT = 'aichat.retention-journal.v1'
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
FIELDS = (
    'thread_id',
    'owner_id',
    'namespace',
    'scope_hash',
    'thread_created_at',
    'deleted_at',
    'reason',
    'message_count',
    'turn_count',
    'had_grants',
)


class JournalError(ValueError):
    """A content-free refusal to trust or apply a journal."""


def journal_source():
    """Require an explicit environment identity, retained on its restore clone."""
    source = os.environ.get('INVENTREE_RETENTION_JOURNAL_SOURCE', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', source):
        raise JournalError('Configure a valid retention journal source identity')
    return source


def aware_time(value):
    """Parse an explicit timezone-aware restore timestamp."""
    try:
        result = parse_datetime(value)
        if result is not None and not timezone.is_naive(result):
            return result
    except (TypeError, ValueError):
        pass
    raise JournalError('A timezone-aware timestamp is required')


def _reference(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value)


def export_journal(*, since):
    """Export retained deletions since the restore point plus all owed cleanup.

    Old tombstones referenced by unfinished outbox rows are included as well.
    Refuse oversized exports rather than silently truncating their coverage.
    """
    source = journal_source()
    lower = aware_time(since)
    upper = timezone.now()
    if not upper - timedelta(days=retention.RETENTION_TOMBSTONE_DAYS) <= lower <= upper:
        raise JournalError('Restore point is outside the retained journal window')
    pending = AIRetentionOutbox.objects.filter(created_at__lte=upper).exclude(
        state='done'
    )
    obligations = list(
        pending
        .order_by('kind', 'reference')
        .values('kind', 'reference')
        .distinct()[: MAX_ROWS + 1]
    )
    tombstones = ChatThreadTombstone.objects.filter(deleted_at__lte=upper).filter(
        Q(deleted_at__gte=lower) | Q(thread_id__in=pending.values('reference'))
    )
    rows = list(tombstones.order_by('thread_id').values(*FIELDS)[: MAX_ROWS + 1])
    if len(rows) + len(obligations) > MAX_ROWS:
        raise JournalError(
            'Journal exceeds the record limit; no partial export was created'
        )
    for row in rows:
        for key in ('thread_created_at', 'deleted_at'):
            row[key] = row[key].isoformat()
    payload = {
        'schema_version': 1,
        'source': source,
        'since': lower.isoformat(),
        'as_of': upper.isoformat(),
        'scope': 'retained_thread_deletions',
        'kinds': sorted(retention.OUTBOX_KINDS),
        'threads': rows,
        'outbox': obligations,
    }
    token = signing.dumps(payload, salt=SALT, compress=False)
    if len(token.encode('utf-8')) > MAX_BYTES:
        raise JournalError('Journal exceeds the byte limit')
    # Reuse the import schema validator before returning an artifact.
    read_journal(token, since=since)
    return token


def read_journal(token, *, since):
    """Verify the entire bounded artifact before any application writes."""
    if (
        not isinstance(token, str)
        or len(token.encode('utf-8')) > MAX_BYTES
        or token.startswith('.')
    ):
        raise JournalError('Invalid journal size or encoding')
    try:
        payload = signing.loads(token, salt=SALT)
    except Exception:
        raise JournalError('Journal signature or encoding is invalid') from None
    try:
        if set(payload) != {
            'schema_version',
            'source',
            'since',
            'as_of',
            'scope',
            'kinds',
            'threads',
            'outbox',
        }:
            raise ValueError
        if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
            raise ValueError
        if (
            payload['source'] != journal_source()
            or payload['scope'] != 'retained_thread_deletions'
        ):
            raise ValueError
        lower, upper = aware_time(payload['since']), aware_time(payload['as_of'])
        if lower != aware_time(since) or lower > upper or upper > timezone.now():
            raise ValueError
        rows, pending, kinds = payload['threads'], payload['outbox'], payload['kinds']
        if not all(isinstance(items, list) for items in (rows, pending, kinds)):
            raise ValueError
        if len(rows) + len(pending) > MAX_ROWS or len(kinds) > 1000:
            raise ValueError
        if not all(
            isinstance(k, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,31}', k)
            for k in kinds
        ):
            raise ValueError
        if len(set(kinds)) != len(kinds):
            raise ValueError
        references = set()
        for row in rows:
            if (
                set(row) != set(FIELDS)
                or not _reference(row['thread_id'])
                or row['thread_id'] in references
            ):
                raise ValueError
            references.add(row['thread_id'])
            if row['owner_id'] is not None and (
                type(row['owner_id']) is not int or row['owner_id'] <= 0
            ):
                raise ValueError
            if (
                row['namespace'] != 'unscoped'
                or not isinstance(row['scope_hash'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', row['scope_hash'])
            ):
                raise ValueError
            if row['reason'] not in {'user_delete', 'user_erasure', 'retention_expiry'}:
                raise ValueError
            if (
                not aware_time(row['thread_created_at'])
                <= aware_time(row['deleted_at'])
                <= upper
            ):
                raise ValueError
            if type(row['had_grants']) is not bool or any(
                type(row[k]) is not int or row[k] < 0
                for k in ('message_count', 'turn_count')
            ):
                raise ValueError
        pairs = set()
        for row in pending:
            if (
                set(row) != {'kind', 'reference'}
                or row['kind'] not in kinds
                or not _reference(row['reference'])
            ):
                raise ValueError
            pair = (row['kind'], row['reference'])
            if pair in pairs:
                raise ValueError
            pairs.add(pair)
    except (ValueError, TypeError, KeyError):
        raise JournalError(
            'Journal schema, source or restore point is invalid'
        ) from None
    return payload


def _same_thread(existing, row, *, tombstone=False):
    return (
        existing.namespace == row['namespace']
        and existing.scope_hash == row['scope_hash']
        and (
            existing.owner_id == row['owner_id']
            or (tombstone and existing.owner_id is None)
        )
        and (existing.thread_created_at if tombstone else existing.created_at)
        == aware_time(row['thread_created_at'])
    )


def replay_journal(token, *, since, execute=False):
    """Preflight the full journal, then replay only its targets under serving hold.

    This never lifts the hold. A clean result is scoped to this artifact and
    must be combined with source/cutover and worker-isolation evidence.
    """
    payload = read_journal(token, since=since)
    if execute and not restore_hold_enabled():
        raise JournalError('Replay execution requires INVENTREE_RESTORE_HOLD')
    gaps = retention.THREAD_DERIVATIVES.uncovered_models()
    unknown = set(payload['kinds']) - retention.OUTBOX_KINDS.keys()
    thread_kinds = {
        entry.outbox_kind for entry in retention.THREAD_DERIVATIVES.entries
    } | {'thread_summary'}
    # A future non-thread outbox kind needs an explicit journal/replay contract.
    unknown |= retention.OUTBOX_KINDS.keys() - thread_kinds
    conflicts = unsupported = 0
    references = {row['thread_id'] for row in payload['threads']}
    for row in payload['threads']:
        root = ChatThread.objects.filter(pk=row['thread_id']).first()
        stone = ChatThreadTombstone.objects.filter(thread_id=row['thread_id']).first()
        if (root and not _same_thread(root, row)) or (
            stone and not _same_thread(stone, row, tombstone=True)
        ):
            conflicts += 1
    for row in payload['outbox']:
        if row['reference'] not in references:
            # A live summary correction needs its lost exclusion payload. A
            # reference alone cannot reconstruct it, nor authorize root erasure.
            if (
                row['kind'] != 'upload_dir'
                or ChatThread.objects.filter(pk=row['reference']).exists()
            ):
                unsupported += 1
    report = {
        'schema_version': 1,
        'scope': payload['scope'],
        'journal_sha256': hashlib.sha256(token.encode()).hexdigest(),
        'since': payload['since'],
        'as_of': payload['as_of'],
        'threads': len(references),
        'outbox': len(payload['outbox']),
        'conflicts': conflicts,
        'unsupported_obligations': unsupported,
        'unknown_kinds': len(unknown),
        'registration_gaps': len(gaps),
        'serving_hold': restore_hold_enabled(),
        'hold_released': False,
    }
    if conflicts or unsupported or unknown or gaps:
        return {**report, 'status': 'replay_incomplete'}
    if not execute:
        return {**report, 'status': 'dry_run'}
    failed = completed = 0
    for row in payload['threads']:
        ref = row['thread_id']
        try:
            root = ChatThread.objects.filter(pk=ref).first()
            if root:
                if not _same_thread(root, row):
                    raise JournalError('Thread identity changed during replay')
                retention.purge_thread_now(ref, reason=row['reason'])
            with transaction.atomic():
                values = {
                    key: value for key, value in row.items() if key != 'thread_id'
                }
                values['thread_created_at'] = aware_time(values['thread_created_at'])
                deleted_at = aware_time(values.pop('deleted_at'))
                if not get_user_model().objects.filter(pk=values['owner_id']).exists():
                    values['owner_id'] = None
                stone, _ = ChatThreadTombstone.objects.get_or_create(
                    thread_id=ref, defaults=values
                )
                if not _same_thread(stone, row, tombstone=True):
                    raise JournalError('Tombstone identity changed during replay')
                # Replay must not restart the journal's retention clock.
                if stone.deleted_at > deleted_at:
                    ChatThreadTombstone.objects.filter(pk=stone.pk).update(
                        deleted_at=deleted_at
                    )
                for kind in thread_kinds:
                    retention.enqueue_outbox(kind, ref)
            receipt = retention.thread_purge_receipt(ref)
            if receipt['status'] != 'deleted':
                failed += 1
            else:
                completed += 1
        except Exception:
            failed += 1
    for row in payload['outbox']:
        if row['reference'] in references:
            continue
        try:
            ref, kind = row['reference'], row['kind']
            if ChatThread.objects.filter(pk=ref).exists():
                raise JournalError('Orphan reference acquired a live root')
            retention.enqueue_outbox(kind, ref)
            cleanup = retention.OUTBOX_KINDS[kind]
            cleanup.handler(ref)
            if retention.checked_residual(cleanup.probe, ref):
                raise JournalError('Residual remains')
            AIRetentionOutbox.objects.filter(kind=kind, reference=ref).exclude(
                state='done'
            ).update(state='done', completed_at=timezone.now(), last_error_code='')
        except Exception:
            failed += 1
    return {
        **report,
        'status': 'replay_incomplete' if failed else 'replayed',
        'completed_threads': completed,
        'failures': failed,
        'finished_at': timezone.now().isoformat(),
    }
