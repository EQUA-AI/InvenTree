"""Signed, bounded deletion journals for an isolated restored database.

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

from aichat.models import (
    AccountErasureTombstone,
    AIRetentionOutbox,
    ChatThread,
    ChatThreadTombstone,
)
from aichat.services import client_memory_erasure, memory_journal, retention
from aichat.services.account_erasure import erase_account
from aichat.services.account_erasure_log import record_erasure
from InvenTree.restore_hold import restore_hold_enabled

SALT = 'aichat.retention-journal.v1'
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
ACCOUNT_FIELDS = ('user_id', 'user_joined_at', 'requested_at')
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
    'forget_confirmed_memories',
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

    Old thread tombstones referenced by unfinished outbox rows are included too.
    Include every retained account intent, even before the restore point: an
    intent is not proof its credential/content cleanup ever finished.
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
    accounts = list(
        AccountErasureTombstone.objects
        .filter(requested_at__lte=upper)
        .order_by('user_id')
        .values(*ACCOUNT_FIELDS)[: MAX_ROWS + 1]
    )
    try:
        memories = memory_journal.export_memory(
            upper=upper, pending=obligations, limit=MAX_ROWS
        )
    except ValueError:
        raise JournalError('Memory deletion proof is incomplete') from None
    try:
        client_memories = client_memory_erasure.export_proof(limit=MAX_ROWS)
    except ValueError:
        raise JournalError('Client memory deletion proof is incomplete') from None
    memory_count = sum(
        len(memories[key]) for key in ('tombstones', 'opt_outs', 'purges')
    )
    if (
        len(rows)
        + len(obligations)
        + len(accounts)
        + memory_count
        + len(client_memories)
        > MAX_ROWS
    ):
        raise JournalError(
            'Journal exceeds the record limit; no partial export was created'
        )
    for row in rows:
        for key in ('thread_created_at', 'deleted_at'):
            row[key] = row[key].isoformat()
    for row in accounts:
        for key in ('user_joined_at', 'requested_at'):
            row[key] = row[key].isoformat()
    payload = {
        'schema_version': 5,
        'source': source,
        'since': lower.isoformat(),
        'as_of': upper.isoformat(),
        'scope': 'retained_threads_accounts_and_memory',
        'kinds': sorted(retention.OUTBOX_KINDS),
        'threads': rows,
        'outbox': obligations,
        'accounts': accounts,
        'memories': memories,
        'client_memories': client_memories,
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
        if not isinstance(payload, dict):
            raise ValueError
        version = payload.get('schema_version')
        if type(version) is not int or version not in (1, 2, 3, 4, 5):
            raise ValueError
        fields = {
            'schema_version',
            'source',
            'since',
            'as_of',
            'scope',
            'kinds',
            'threads',
            'outbox',
        }
        if version >= 2:
            fields.add('accounts')
        if version >= 3:
            fields.add('memories')
        if version >= 5:
            fields.add('client_memories')
        if set(payload) != fields:
            raise ValueError
        scope = (
            'retained_threads_accounts_and_memory'
            if version >= 3
            else 'retained_threads_and_local_account_intents'
            if version == 2
            else 'retained_thread_deletions'
        )
        if payload['source'] != journal_source() or payload['scope'] != scope:
            raise ValueError
        lower, upper = aware_time(payload['since']), aware_time(payload['as_of'])
        if lower != aware_time(since) or lower > upper or upper > timezone.now():
            raise ValueError
        rows, pending, kinds = payload['threads'], payload['outbox'], payload['kinds']
        accounts = payload.get('accounts', [])
        if not all(
            isinstance(items, list) for items in (rows, pending, kinds, accounts)
        ):
            raise ValueError
        memory_count = (
            memory_journal.validate_memory(
                payload['memories'], upper=upper, parse_time=aware_time
            )
            if version >= 3
            else 0
        )
        client_count = client_memory_erasure.validate_proof(
            payload.get('client_memories', []), upper=upper, parse_time=aware_time
        )
        if (
            len(rows) + len(pending) + len(accounts) + memory_count + client_count
            > MAX_ROWS
            or len(kinds) > 1000
        ):
            raise ValueError
        if not all(
            isinstance(k, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,31}', k)
            for k in kinds
        ):
            raise ValueError
        if len(set(kinds)) != len(kinds):
            raise ValueError
        subjects = set()
        for row in accounts:
            if (
                set(row) != set(ACCOUNT_FIELDS)
                or type(row['user_id']) is not int
                or not 0 < row['user_id'] <= 9223372036854775807
                or row['user_id'] in subjects
                or not aware_time(row['user_joined_at'])
                <= aware_time(row['requested_at'])
                <= upper
            ):
                raise ValueError
            subjects.add(row['user_id'])
        references = set()
        for row in rows:
            if (
                set(row) != set(FIELDS if version >= 4 else FIELDS[:-1])
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
            if version >= 4 and type(row['forget_confirmed_memories']) is not bool:
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
                or not (
                    _reference(row['reference'])
                    or (
                        version >= 3
                        and row['kind'] == 'memory_owner_purge'
                        and isinstance(row['reference'], str)
                        and len(row['reference']) <= 255
                    )
                )
            ):
                raise ValueError
            pair = (row['kind'], row['reference'])
            if pair in pairs:
                raise ValueError
            pairs.add(pair)
        client_refs = {
            str(row['client_id']) for row in payload.get('client_memories', [])
        }
        if any(
            row['kind'] == client_memory_erasure.KIND
            and row['reference'] not in client_refs
            for row in pending
        ):
            raise ValueError
        if version >= 3:
            proof_refs = {row['reference'] for row in payload['memories']['purges']}
            outbox_refs = {
                row['reference']
                for row in pending
                if row['kind'] == 'memory_owner_purge'
            }
            if proof_refs != outbox_refs:
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
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


def _account_conflict(row):
    user = get_user_model().objects.filter(pk=row['user_id']).first()
    stone = AccountErasureTombstone.objects.filter(user_id=row['user_id']).first()
    joined = aware_time(row['user_joined_at'])
    return (user is not None and user.date_joined != joined) or (
        stone is not None and stone.user_joined_at != joined
    )


def _replay_account(row):
    """Preserve missing-account intents or disable a matching restored account."""
    user_id = row['user_id']
    joined, requested = (
        aware_time(row['user_joined_at']),
        aware_time(row['requested_at']),
    )
    # Restore isolation is mandatory: a missing PK cannot be row-locked against
    # arbitrary future insertion. Preserve its obligation without creating a user.
    with transaction.atomic(durable=True):
        user = get_user_model().objects.select_for_update().filter(pk=user_id).first()
        if user is None:
            record_erasure(
                user_id=user_id, user_joined_at=joined, requested_at=requested
            )
            return True
        if user.date_joined != joined:
            raise JournalError('Account identity changed during replay')
    # erase_account owns its durable disable/intent commit; do not nest it.
    result = erase_account(user_id, expected_joined_at=joined, requested_at=requested)
    return result['status'] == 'purged'


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
    } | {'thread_summary', 'thread_root'}
    # A future non-thread outbox kind needs an explicit journal/replay contract.
    known_non_thread = (
        {'memory_owner_purge'} if payload['schema_version'] >= 3 else set()
    )
    if payload['schema_version'] >= 5:
        known_non_thread.add(client_memory_erasure.KIND)
    unknown |= retention.OUTBOX_KINDS.keys() - thread_kinds - known_non_thread
    conflicts = unsupported = 0
    accounts = payload.get('accounts', [])
    account_conflicts = sum(bool(_account_conflict(row)) for row in accounts)
    client_memories = payload.get('client_memories', [])
    client_conflicts = client_memory_erasure.conflicts(
        client_memories, parse_time=aware_time
    )
    memories = payload.get('memories')
    try:
        memory_conflicts = (
            memory_journal.conflicts(memories, parse_time=aware_time) if memories else 0
        )
    except ValueError:
        memory_conflicts = 1
    references = {row['thread_id'] for row in payload['threads']}
    for row in payload['threads']:
        root = ChatThread.objects.filter(pk=row['thread_id']).first()
        stone = ChatThreadTombstone.objects.filter(thread_id=row['thread_id']).first()
        if (root and not _same_thread(root, row)) or (
            stone and not _same_thread(stone, row, tombstone=True)
        ):
            conflicts += 1
    for row in payload['outbox']:
        if row['kind'] in known_non_thread:
            continue
        if row['reference'] not in references:
            # A live summary correction needs its lost exclusion payload. A
            # reference alone cannot reconstruct it, nor authorize root erasure.
            if (
                row['kind'] != 'upload_dir'
                or ChatThread.objects.filter(pk=row['reference']).exists()
            ):
                unsupported += 1
    report = {
        'schema_version': 3,
        'journal_schema_version': payload['schema_version'],
        'scope': payload['scope'],
        'journal_sha256': hashlib.sha256(token.encode()).hexdigest(),
        'since': payload['since'],
        'as_of': payload['as_of'],
        'threads': len(references),
        'outbox': len(payload['outbox']),
        'accounts': len(accounts),
        'account_conflicts': account_conflicts,
        'memory_conflicts': memory_conflicts,
        'client_memory_conflicts': int(client_conflicts),
        'client_memory_journal_missing': payload['schema_version'] < 5,
        'memory_journal_missing': payload['schema_version'] < 3,
        'memory_tombstones': len(memories['tombstones']) if memories else 0,
        'memory_opt_outs': len(memories['opt_outs']) if memories else 0,
        'account_journal_missing': payload['schema_version'] == 1,
        'account_erasure_complete': False,
        'conflicts': conflicts,
        'unsupported_obligations': unsupported,
        'unknown_kinds': len(unknown),
        'registration_gaps': len(gaps),
        'serving_hold': restore_hold_enabled(),
        'hold_released': False,
    }
    if (
        conflicts
        or account_conflicts
        or unsupported
        or unknown
        or gaps
        or report['account_journal_missing']
        or memory_conflicts
        or report['memory_journal_missing']
        or client_conflicts
        or report['client_memory_journal_missing']
    ):
        return {**report, 'status': 'replay_incomplete'}
    if not execute:
        return {**report, 'status': 'dry_run'}
    failed = completed = 0
    completed_memories = memory_failures = 0
    for row in client_memories:
        try:
            if not client_memory_erasure.replay(row, parse_time=aware_time):
                memory_failures += 1
        except Exception:
            memory_failures += 1
    for family, handler in (
        ('tombstones', memory_journal.replay_tombstone),
        ('opt_outs', memory_journal.replay_opt_out),
    ):
        for row in memories[family]:
            try:
                if handler(row, parse_time=aware_time):
                    completed_memories += 1
                else:
                    memory_failures += 1
            except Exception:
                memory_failures += 1
    completed_accounts = account_failures = 0
    for row in accounts:
        try:
            if not _replay_account(row):
                account_failures += 1
            else:
                completed_accounts += 1
        except Exception:
            account_failures += 1
    for row in payload['threads']:
        ref = row['thread_id']
        try:
            root = ChatThread.objects.filter(pk=ref).first()
            if root:
                if not _same_thread(root, row):
                    raise JournalError('Thread identity changed during replay')
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
                if (
                    row.get('forget_confirmed_memories', False)
                    and not stone.forget_confirmed_memories
                ):
                    stone.forget_confirmed_memories = True
                    stone.save(update_fields=['forget_confirmed_memories'])
                # Replay must not restart the journal's retention clock.
                if stone.deleted_at > deleted_at:
                    ChatThreadTombstone.objects.filter(pk=stone.pk).update(
                        deleted_at=deleted_at
                    )
                for kind in thread_kinds:
                    retention.enqueue_outbox(kind, ref)
            if root:
                retention.purge_thread_now(
                    ref,
                    reason=row['reason'],
                    forget_confirmed=row.get('forget_confirmed_memories', False),
                )
            receipt = retention.thread_purge_receipt(ref)
            if receipt['status'] != 'deleted':
                failed += 1
            else:
                completed += 1
        except Exception:
            failed += 1
    for row in payload['outbox']:
        if row['kind'] not in known_non_thread and row['reference'] in references:
            continue
        try:
            ref, kind = row['reference'], row['kind']
            if (
                kind not in known_non_thread
                and ChatThread.objects.filter(pk=ref).exists()
            ):
                raise JournalError('Orphan reference acquired a live root')
            retention.enqueue_outbox(kind, ref)
            cleanup = retention.OUTBOX_KINDS[kind]
            if kind == 'memory_owner_purge':
                proof = next(
                    row for row in memories['purges'] if row['reference'] == ref
                )
                memory_journal.replay_purge(proof, parse_time=aware_time)
            else:
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
        'status': 'replay_incomplete'
        if failed or account_failures or memory_failures
        else 'replayed',
        'completed_accounts': completed_accounts,
        'account_failures': account_failures,
        'completed_threads': completed,
        'completed_memories': completed_memories,
        'memory_failures': memory_failures,
        'failures': failed + account_failures + memory_failures,
        'finished_at': timezone.now().isoformat(),
    }
