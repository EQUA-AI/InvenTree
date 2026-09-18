"""Content-free memory deletion proof embedded in signed restore journals.

Never used on the serving request path. Replay requires an isolated held restore
and the same fingerprint key. Historical missing account identity is a blocker,
not an invitation to infer or backfill it from a possibly reused user ID.
"""

import re
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction

from aichat.models import (
    MemoryFact,
    MemoryFactTombstone,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from aichat.services import memory_lifecycle as lifecycle
from InvenTree.restore_hold import restore_hold_enabled

FIELDS = (
    'id',
    'owner_id',
    'owner_joined_at',
    'client_code',
    'slot_fingerprint',
    'claim_fingerprint',
    'source_fingerprint',
    'source_sequence',
    'fact_id',
    'deleted_at',
    'reason',
)
OWNER_FIELDS = {'owner_id', 'owner_joined_at', 'requested_at'}
HEX = re.compile(r'[a-f0-9]{64}')


def export_memory(*, upper, pending, limit):
    """Export all retained proof and pending owner obligations, without text."""
    stones = list(
        MemoryFactTombstone.objects
        .filter(deleted_at__lte=upper)
        .order_by('id')
        .values(*FIELDS)[: limit + 1]
    )
    opt_outs = list(
        UserMemorySettings.objects
        .filter(opted_out=True, updated_at__lte=upper)
        .order_by('user_id')
        .values('user_id', 'user__date_joined', 'updated_at')[: limit + 1]
    )
    opt_outs = [
        {
            'owner_id': row['user_id'],
            'owner_joined_at': row['user__date_joined'].isoformat(),
            'requested_at': row['updated_at'].isoformat(),
        }
        for row in opt_outs
    ]
    purges = []
    for row in pending:
        if row['kind'] != 'memory_owner_purge':
            continue
        owner_id, cutoff, _reason = lifecycle._parse_purge_reference(row['reference'])
        joined = (
            get_user_model()
            .objects.filter(pk=owner_id)
            .values_list('date_joined', flat=True)
            .first()
        )
        if joined is None:
            raise ValueError('Memory purge owner identity unavailable')
        purges.append({
            'owner_id': owner_id,
            'owner_joined_at': joined.isoformat(),
            'requested_at': cutoff.isoformat(),
            'reference': row['reference'],
        })
    for row in stones:
        if row['owner_joined_at'] is None:
            raise ValueError('Memory tombstone owner identity unavailable')
        for key in ('id', 'fact_id'):
            row[key] = str(row[key]) if row[key] is not None else None
        for key in ('owner_joined_at', 'deleted_at'):
            row[key] = row[key].isoformat()
    return {
        'tombstones': stones,
        'opt_outs': opt_outs,
        'purges': purges,
        'fingerprint_key_check': lifecycle.fingerprint('journal-key-check-v1', 'memory')
        if stones
        else None,
    }


def validate_memory(payload, *, upper, parse_time):
    """Validate structure before any side effects; return the combined row count."""
    if not isinstance(payload, dict) or set(payload) != {
        'tombstones',
        'opt_outs',
        'purges',
        'fingerprint_key_check',
    }:
        raise ValueError('Invalid memory journal')
    if any(
        not isinstance(payload[key], list)
        for key in ('tombstones', 'opt_outs', 'purges')
    ):
        raise ValueError('Invalid memory journal')
    key = payload['fingerprint_key_check']
    if (
        payload['tombstones']
        and (not isinstance(key, str) or HEX.fullmatch(key) is None)
    ) or (not payload['tombstones'] and key is not None):
        raise ValueError('Invalid fingerprint key identity')
    seen = set()
    slots = set()
    for row in payload['tombstones']:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('Invalid memory tombstone')
        identity = str(uuid.UUID(row['id']))
        if identity != row['id'] or identity in seen:
            raise ValueError('Invalid memory tombstone identity')
        seen.add(identity)
        _owner(row, parse_time(row['deleted_at']), upper, parse_time)
        if (
            row['fact_id'] is not None
            and str(uuid.UUID(row['fact_id'])) != row['fact_id']
        ):
            raise ValueError('Invalid memory target')
        if not isinstance(row['client_code'], str) or len(row['client_code']) > 64:
            raise ValueError('Invalid memory client')
        if any(
            not isinstance(row[k], str) or HEX.fullmatch(row[k]) is None
            for k in ('slot_fingerprint', 'claim_fingerprint', 'source_fingerprint')
        ):
            raise ValueError('Invalid memory fingerprints')
        if (
            type(row['source_sequence']) is not int
            or not 0 <= row['source_sequence'] <= 9223372036854775807
        ):
            raise ValueError('Invalid memory source sequence')
        if row['reason'] not in lifecycle.FORGET_REASONS | {'supersede'}:
            raise ValueError('Invalid memory deletion reason')
        natural = (
            row['owner_id'],
            row['client_code'],
            row['slot_fingerprint'],
            row['claim_fingerprint'],
            row['fact_id'],
        )
        if natural in slots:
            raise ValueError('Duplicate memory tombstone')
        slots.add(natural)
    for family in ('opt_outs', 'purges'):
        seen = set()
        for row in payload[family]:
            fields = OWNER_FIELDS | ({'reference'} if family == 'purges' else set())
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError('Invalid memory owner proof')
            requested = parse_time(row['requested_at'])
            _owner(row, requested, upper, parse_time)
            identity = row.get('reference', row['owner_id'])
            if identity in seen:
                raise ValueError('Duplicate memory owner proof')
            seen.add(identity)
            if family == 'purges':
                owner_id, cutoff, _ = lifecycle._parse_purge_reference(row['reference'])
                if owner_id != row['owner_id'] or cutoff != requested:
                    raise ValueError('Invalid memory purge identity')
    return sum(len(payload[key]) for key in ('tombstones', 'opt_outs', 'purges'))


def _owner(row, requested, upper, parse_time):
    if (
        type(row['owner_id']) is not int
        or not 0 < row['owner_id'] <= 9223372036854775807
    ):
        raise ValueError('Invalid memory owner')
    if not parse_time(row['owner_joined_at']) <= requested <= upper:
        raise ValueError('Invalid memory owner time')


def conflicts(payload, *, parse_time):
    """Check account incarnation, immutable identity and fingerprint-key continuity."""
    count = 0
    if payload['tombstones'] and payload[
        'fingerprint_key_check'
    ] != lifecycle.fingerprint('journal-key-check-v1', 'memory'):
        return 1
    for row in payload['tombstones'] + payload['opt_outs'] + payload['purges']:
        joined = (
            get_user_model()
            .objects.filter(pk=row['owner_id'])
            .values_list('date_joined', flat=True)
            .first()
        )
        if joined is not None and joined != parse_time(row['owner_joined_at']):
            count += 1
    for row in payload['tombstones']:
        stone = MemoryFactTombstone.objects.filter(pk=row['id']).first()
        natural = {
            key: row[key]
            for key in (
                'owner_id',
                'client_code',
                'slot_fingerprint',
                'claim_fingerprint',
                'fact_id',
            )
        }
        if stone and (
            any(
                (
                    str(getattr(stone, key))
                    if key == 'fact_id' and getattr(stone, key) is not None
                    else getattr(stone, key)
                )
                != value
                for key, value in natural.items()
            )
            or stone.owner_joined_at != parse_time(row['owner_joined_at'])
        ):
            count += 1
        if MemoryFactTombstone.objects.filter(**natural).exclude(pk=row['id']).exists():
            count += 1
        fact = MemoryFact.objects.filter(pk=row['fact_id']).first()
        if fact and (
            fact.owner_id != row['owner_id']
            or lifecycle.slot_fingerprint(fact) != row['slot_fingerprint']
            or fact.claim_fingerprint != row['claim_fingerprint']
        ):
            count += 1
    return count


@transaction.atomic
def replay_tombstone(row, *, parse_time):
    """Preserve source proof and scrub resurrected matching claims under hold."""
    if not restore_hold_enabled():
        raise ValueError('Memory replay requires restore hold')
    owner = (
        get_user_model().objects.select_for_update().filter(pk=row['owner_id']).first()
    )
    if owner and owner.date_joined != parse_time(row['owner_joined_at']):
        raise ValueError('Memory owner identity changed')
    values = dict(row)
    values.pop('id')
    values['owner_joined_at'] = parse_time(values['owner_joined_at'])
    values['deleted_at'] = parse_time(values['deleted_at'])
    stone, _ = MemoryFactTombstone.objects.get_or_create(pk=row['id'], defaults=values)
    if stone.deleted_at > values['deleted_at']:
        values['deleted_at'] = stone.deleted_at
    cutoff = values['deleted_at']
    # Bounded cleanup is retried by rerunning the same immutable journal.
    rows = (
        MemoryFact.objects
        .select_for_update()
        .filter(
            owner_id=row['owner_id'],
            created_at__lte=cutoff,
            lifecycle_state__in=lifecycle.LIVE_STATES,
        )
        .order_by('pk')
    )
    matched = 0
    for fact in rows.iterator(chunk_size=200):
        match = (
            str(fact.pk) == row['fact_id']
            if row['reason'] == 'supersede'
            else (
                fact.claim_fingerprint == row['claim_fingerprint']
                or (
                    fact.client_code == row['client_code']
                    and lifecycle.slot_fingerprint(fact) == row['slot_fingerprint']
                )
            )
        )
        if not match:
            continue
        if matched >= 200:
            return False
        matched += 1
        if row['reason'] == 'supersede':
            if fact.lifecycle_state == 'superseded':
                continue
            fact.lifecycle_state, fact.embedding = 'superseded', None
            fact.version += 1
            fact.save(
                update_fields=['lifecycle_state', 'embedding', 'version', 'updated_at']
            )
            lifecycle._scrub_proposals(fact.pk)
        else:
            lifecycle._forget_locked(fact, actor_id=None, reason=row['reason'])
            # _forget_locked is shared with live deletion. Restore its newly
            # written tombstone's original clock rather than extend retention.
            MemoryFactTombstone.objects.filter(
                owner_id=fact.owner_id,
                fact_id=fact.pk,
                client_code=fact.client_code,
                slot_fingerprint=lifecycle.slot_fingerprint(fact),
                claim_fingerprint=fact.claim_fingerprint,
            ).update(deleted_at=cutoff)
    MemoryFactTombstone.objects.filter(pk=stone.pk).update(
        **values, residual_pending=False
    )
    lifecycle._invalidate_summaries(row['owner_id'])
    return True


@transaction.atomic
def replay_opt_out(row, *, parse_time):
    """Restore the withdrawal stop-bit before its bounded cleanup, never consent."""
    if not restore_hold_enabled():
        raise ValueError('Memory replay requires restore hold')
    owner = (
        get_user_model().objects.select_for_update().filter(pk=row['owner_id']).first()
    )
    if owner is None:
        return True
    if owner.date_joined != parse_time(row['owner_joined_at']):
        raise ValueError('Memory owner identity changed')
    UserMemorySettings.objects.update_or_create(
        user=owner, defaults={'opted_out': True}
    )
    UserMemorySettings.objects.filter(user=owner).update(
        updated_at=parse_time(row['requested_at'])
    )
    MemoryNoticeAcknowledgement.objects.filter(user=owner).delete()
    result = lifecycle.forget_owner_facts(
        owner.pk,
        cutoff=parse_time(row['requested_at']),
        reason='opt_out',
        replay_deleted_at=parse_time(row['requested_at']),
    )
    return result['status'] == 'purged'


@transaction.atomic
def replay_purge(row, *, parse_time):
    """Recheck the exact owner incarnation before retrying an imported obligation."""
    if not restore_hold_enabled():
        raise ValueError('Memory replay requires restore hold')
    owner = (
        get_user_model().objects.select_for_update().filter(pk=row['owner_id']).first()
    )
    if owner is None:
        return True
    if owner.date_joined != parse_time(row['owner_joined_at']):
        raise ValueError('Memory owner identity changed')
    owner_id, cutoff, reason = lifecycle._parse_purge_reference(row['reference'])
    result = lifecycle.forget_owner_facts(
        owner_id, cutoff=cutoff, reason=reason, replay_deleted_at=cutoff
    )
    return result['status'] == 'purged'
