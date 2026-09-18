"""Operator-only, client-scoped durable-memory erasure and restore proof.

Client labels do not establish transcript ownership. Mixed/historical transcripts,
voice and native records remain explicit offboarding blockers, never silently
removed by erasing all of a multi-client user's data.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from aichat.models import ChatThread, ClientAISettings, ClientMemoryErasure, MemoryFact
from aichat.services.memory_lifecycle import LIVE_STATES, forget_owner_facts
from assets.models import Client, ClientScopeGrant
from InvenTree.restore_hold import restore_hold_enabled

KIND = 'memory_client_purge'
FIELDS = ('client_id', 'client_code', 'client_created_at', 'requested_at')


def _rows(stone):
    return MemoryFact.objects.filter(
        client_code=stone.client_code, lifecycle_state__in=LIVE_STATES
    )


def _identity(stone, client):
    return (
        stone.client_id == client.pk
        and stone.client_code == client.code
        and stone.client_created_at == client.created_at
    )


def _stone(reference):
    if (
        not isinstance(reference, str)
        or not reference.isascii()
        or not reference.isdecimal()
        or len(reference) > 20
    ):
        raise ValueError('Invalid client erasure reference')
    return ClientMemoryErasure.objects.get(client_id=int(reference))


def residual(reference):
    """Unknown/non-client references are ignored by thread receipt probing."""
    if (
        not str(reference).isascii()
        or not str(reference).isdecimal()
        or len(str(reference)) > 20
    ):
        return 0
    stone = ClientMemoryErasure.objects.filter(client_id=int(reference)).first()
    if stone is None:
        return 0
    return (
        _rows(stone).count()
        + ClientAISettings.objects.filter(
            client_id=stone.client_id, memory_enabled=True
        ).count()
    )


def retry(reference):
    """At most 200 facts per pass, owner locks and the common forget operation."""
    stone = _stone(reference)
    client = Client.objects.filter(pk=stone.client_id).first()
    if client is not None and not _identity(stone, client):
        raise ValueError('Client erasure identity mismatch')
    ClientAISettings.objects.filter(client_id=stone.client_id).update(
        memory_enabled=False, enabled_at=None
    )
    remaining = 200
    owners = list(
        _rows(stone)
        .order_by('owner_id')
        .values_list('owner_id', flat=True)
        .distinct()[:200]
    )
    for owner_id in owners:
        if remaining <= 0:
            break
        result = forget_owner_facts(
            owner_id,
            cutoff=timezone.now(),
            client_code=stone.client_code,
            reason='client_purge',
            limit=remaining,
            replay_deleted_at=stone.requested_at if restore_hold_enabled() else None,
        )
        remaining -= result['forgotten']


def purge_client(client_id, *, dry_run=True):
    """Stop learned recall/admission before bounded erasure; preserve other clients."""
    from aichat.services.retention import enqueue_outbox

    if type(client_id) is not int or client_id <= 0:
        raise ValueError('A positive client id is required')
    client = Client.objects.get(pk=client_id)
    owners = ClientScopeGrant.objects.filter(client=client).values('user_id')
    report = {
        'scope': 'client_durable_memory',
        'subject_hash': salted_hmac(
            'aichat.client-erasure.v1', str(client_id)
        ).hexdigest(),
        'client_erasure_complete': False,
        'retained_current_grantees_threads': ChatThread.objects.filter(
            owner_id__in=owners
        ).count(),
        'not_covered': [
            'transcripts_without_exclusive_client_lineage',
            'mixed_client_transcripts',
            'voice_and_attachment_sources',
            'native_operational_records',
            'external_projections_and_telemetry',
            'backups',
        ],
    }
    if dry_run:
        return {
            **report,
            'status': 'dry_run',
            'remaining_facts': MemoryFact.objects.filter(
                client_code=client.code, lifecycle_state__in=LIVE_STATES
            ).count(),
        }
    with transaction.atomic():
        client = Client.objects.select_for_update().get(pk=client_id)
        stone, _ = ClientMemoryErasure.objects.get_or_create(
            client_id=client.pk,
            defaults={
                'client_code': client.code,
                'client_created_at': client.created_at,
                'requested_at': timezone.now(),
            },
        )
        if not _identity(stone, client):
            raise ValueError('Client erasure identity mismatch')
        ClientAISettings.objects.filter(client=client).update(
            memory_enabled=False, enabled_at=None
        )
        enqueue_outbox(KIND, str(client_id))
    retry(str(client_id))
    count = residual(str(client_id))
    return {
        **report,
        'status': 'purge_incomplete',
        'memory_status': 'purge_incomplete' if count else 'purged',
        'remaining_facts': count,
    }


def export_proof(*, limit=10000):
    """Include completed stop bits too; completion never re-enrolls a client."""
    rows = list(
        ClientMemoryErasure.objects.order_by('client_id').values(*FIELDS)[: limit + 1]
    )
    if len(rows) > limit:
        raise ValueError('Client erasure proof exceeds limit')
    for row in rows:
        for key in ('client_created_at', 'requested_at'):
            row[key] = row[key].isoformat()
    return rows


def validate_proof(rows, *, upper, parse_time):
    """Validate all metadata before replay; never infer a reused client identity."""
    import re

    if not isinstance(rows, list):
        raise ValueError('Invalid client erasure proof')
    identities, codes = set(), set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != set(FIELDS)
            or type(row['client_id']) is not int
            or not 0 < row['client_id'] <= 9223372036854775807
            or not isinstance(row['client_code'], str)
            or not re.fullmatch(r'[-a-zA-Z0-9_]{1,64}', row['client_code'])
            or row['client_id'] in identities
            or row['client_code'] in codes
            or not parse_time(row['client_created_at'])
            <= parse_time(row['requested_at'])
            <= upper
        ):
            raise ValueError('Invalid client erasure proof')
        identities.add(row['client_id'])
        codes.add(row['client_code'])
    return len(rows)


def conflicts(rows, *, parse_time):
    """A missing client may stay missing; an id or code reused by another may not."""
    for row in rows:
        existing = list(
            Client.objects.filter(pk=row['client_id'])
            | Client.objects.filter(code=row['client_code'])
        )
        for client in existing:
            if (
                client.pk != row['client_id']
                or client.code != row['client_code']
                or client.created_at != parse_time(row['client_created_at'])
            ):
                return True
        stone = ClientMemoryErasure.objects.filter(client_id=row['client_id']).first()
        if stone and (
            stone.client_code != row['client_code']
            or stone.client_created_at != parse_time(row['client_created_at'])
        ):
            return True
    return False


def replay(row, *, parse_time):
    """Held-only stop-bit replay precedes any restored memory becoming readable."""
    from aichat.services.retention import enqueue_outbox

    if not restore_hold_enabled() or conflicts([row], parse_time=parse_time):
        raise ValueError('Client erasure replay unavailable')
    with transaction.atomic():
        ClientMemoryErasure.objects.get_or_create(
            client_id=row['client_id'],
            defaults={
                'client_code': row['client_code'],
                'client_created_at': parse_time(row['client_created_at']),
                'requested_at': parse_time(row['requested_at']),
            },
        )
        ClientAISettings.objects.filter(client_id=row['client_id']).update(
            memory_enabled=False, enabled_at=None
        )
        enqueue_outbox(KIND, str(row['client_id']))
    retry(str(row['client_id']))
    return residual(str(row['client_id'])) == 0
