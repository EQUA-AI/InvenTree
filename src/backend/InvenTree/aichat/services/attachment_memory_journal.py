"""Content-free proof of attachment claim severance across database restore.

Proof identifies the claim, fact and owner incarnations. It does not identify or
remove attachment files, native records or Search documents. Those retain their
own deletion contracts. No proof is inferred for historical severance.
"""

import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction

from aichat.models import MemoryAttachmentSeverance, MemoryFact, MemoryFactClaim
from InvenTree.restore_hold import restore_hold_enabled

FIELDS = (
    'identity',
    'claim_id',
    'claim_created_at',
    'fact_id',
    'fact_created_at',
    'owner_id',
    'owner_joined_at',
    'attachment_id',
    'severed_at',
)


def identity(claim_id, created_at):
    """Stable hash of the claim incarnation, without source text."""
    return hashlib.sha256(
        json.dumps([claim_id, created_at.isoformat()]).encode()
    ).hexdigest()


def record(claim, *, fact, severed_at):
    """Call under the fact/owner lock, in the same transaction as severance."""
    proof, _ = MemoryAttachmentSeverance.objects.get_or_create(
        identity=identity(claim.pk, claim.created_at),
        defaults={
            'claim_id': claim.pk,
            'claim_created_at': claim.created_at,
            'fact_id': fact.pk,
            'fact_created_at': fact.created_at,
            'owner_id': fact.owner_id,
            'owner_joined_at': fact.owner.date_joined,
            'attachment_id': claim.attachment_id,
            'severed_at': severed_at,
        },
    )
    if (
        proof.fact_id != fact.pk
        or proof.fact_created_at != fact.created_at
        or proof.owner_id != fact.owner_id
        or proof.owner_joined_at != fact.owner.date_joined
        or proof.attachment_id != claim.attachment_id
    ):
        raise ValueError('Attachment claim identity conflict')
    return proof


def export_proof(*, upper, limit):
    """Export every retained proof before the quiescent snapshot clock."""
    rows = list(
        MemoryAttachmentSeverance.objects
        .filter(severed_at__lte=upper)
        .order_by('identity')
        .values(*FIELDS)[: limit + 1]
    )
    if len(rows) > limit:
        raise ValueError('Attachment severance journal exceeds limit')
    for row in rows:
        row['fact_id'] = str(row['fact_id'])
        for key in (
            'claim_created_at',
            'fact_created_at',
            'owner_joined_at',
            'severed_at',
        ):
            row[key] = row[key].isoformat()
    return rows


def validate_proof(rows, *, upper, parse_time):
    """Reject malformed, duplicate or impossible proof before replay writes."""
    if not isinstance(rows, list):
        raise ValueError('Invalid attachment severance proof')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('Invalid attachment severance shape')
        if any(
            type(row[key]) is not int or not 0 < row[key] <= 9223372036854775807
            for key in ('claim_id', 'owner_id', 'attachment_id')
        ):
            raise ValueError('Invalid attachment severance identity')
        if str(uuid.UUID(row['fact_id'])) != row['fact_id']:
            raise ValueError('Invalid fact identity')
        created = parse_time(row['claim_created_at'])
        severed = parse_time(row['severed_at'])
        if (
            not parse_time(row['owner_joined_at'])
            <= parse_time(row['fact_created_at'])
            <= created
            <= severed
            <= upper
            or row['identity'] != identity(row['claim_id'], created)
            or row['identity'] in seen
        ):
            raise ValueError('Invalid attachment severance clocks')
        seen.add(row['identity'])
    return len(rows)


def conflicts(rows, *, parse_time):
    """Missing old rows are safe; reused identities must never be mutated."""
    for row in rows:
        owner = get_user_model().objects.filter(pk=row['owner_id']).first()
        fact = MemoryFact.objects.filter(pk=row['fact_id']).first()
        claim = MemoryFactClaim.objects.filter(pk=row['claim_id']).first()
        proof = MemoryAttachmentSeverance.objects.filter(pk=row['identity']).first()
        if owner and owner.date_joined != parse_time(row['owner_joined_at']):
            return True
        if fact and (
            fact.owner_id != row['owner_id']
            or fact.created_at != parse_time(row['fact_created_at'])
        ):
            return True
        if claim and (
            str(claim.fact_id) != row['fact_id']
            or claim.created_at != parse_time(row['claim_created_at'])
            or claim.attachment_id not in (None, row['attachment_id'])
        ):
            return True
        if proof:
            for key in FIELDS:
                if key == 'severed_at':
                    continue
                value = getattr(proof, key)
                expected = parse_time(row[key]) if key.endswith('_at') else row[key]
                if key == 'fact_id':
                    value = str(value)
                if value != expected:
                    return True
    return False


@transaction.atomic
def replay(row, *, parse_time):
    """Held-only, exact-incarnation replay. No attachment id implies native deletion."""
    from aichat.models import MemoryFactEvent
    from aichat.services.memory_lifecycle import (
        _forget_locked,
        _invalidate_summaries,
        _scrub_proposals,
    )

    if not restore_hold_enabled():
        raise ValueError('Attachment severance replay needs restore hold')
    get_user_model().objects.select_for_update().filter(pk=row['owner_id']).first()
    fact = MemoryFact.objects.select_for_update().filter(pk=row['fact_id']).first()
    if conflicts([row], parse_time=parse_time):
        raise ValueError('Attachment severance identity conflict')
    values = {
        key: parse_time(value) if key.endswith('_at') else value
        for key, value in row.items()
    }
    MemoryAttachmentSeverance.objects.get_or_create(
        identity=row['identity'],
        defaults={key: value for key, value in values.items() if key != 'identity'},
    )
    claim = MemoryFactClaim.objects.filter(
        pk=row['claim_id'], attachment_id=row['attachment_id']
    ).first()
    if claim is None or fact is None:
        return True
    if fact.lifecycle_state == 'proposed':
        _forget_locked(
            fact, actor_id=None, reason='forget', deleted_at=values['severed_at']
        )
    else:
        _scrub_proposals(fact.pk)
        MemoryFactClaim.objects.filter(pk=claim.pk).update(
            attachment_id=None,
            source_thread=None,
            source_message=None,
            source_model='',
            source_object_id='',
            source_field='',
            severed_at=values['severed_at'],
        )
        fact.source_thread = None
        fact.source_message = None
        fact.version += 1
        fact.save(
            update_fields=['source_thread', 'source_message', 'version', 'updated_at']
        )
        MemoryFactEvent.objects.create(
            owner_id=fact.owner_id,
            fact_id=fact.pk,
            action='sever',
            version=fact.version,
        )
    _invalidate_summaries(fact.owner_id)
    return not MemoryFactClaim.objects.filter(
        pk=row['claim_id'], attachment_id=row['attachment_id']
    ).exists()
