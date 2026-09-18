"""Fail-closed attachment deletion lineage for existing durable memory claims.

No background success is reported before source severance. Oversized source sets
require bounded operator cleanup while the attachment itself remains available.
The current writer does not admit attachment evidence; any future writer must
serialize on the attachment row before creating a claim.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from aichat.models import MemoryFact, MemoryFactClaim, MemoryFactEvent
from aichat.services.memory_lifecycle import (
    _forget_locked,
    _invalidate_summaries,
    _scrub_proposals,
)

LIMIT = 200


def residual(attachment_id):
    """Confirmed claims are clean only after attachment pointers are severed."""
    return MemoryFactClaim.objects.filter(attachment_id=attachment_id).count()


@transaction.atomic
def cleanup(attachment_id, *, require_complete=False):
    """Withdraw proposals with restore proof, retain confirmed facts, sever sources."""
    if type(attachment_id) is not int or attachment_id < 1:
        raise ValueError('A positive attachment id is required')
    from common.models import Attachment

    Attachment.objects.select_for_update().filter(pk=attachment_id).first()
    claims = list(
        MemoryFactClaim.objects.filter(attachment_id=attachment_id).order_by('pk')[
            : LIMIT + 1
        ]
    )
    if require_complete and len(claims) > LIMIT:
        raise ValueError(
            'Attachment memory cleanup needs bounded operator passes before deletion'
        )
    claims = claims[:LIMIT]
    identities = {claim.fact_id for claim in claims}
    selected_claims = {claim.pk for claim in claims}
    owners = MemoryFact.objects.filter(pk__in=identities).values('owner_id')
    list(
        get_user_model()
        .objects.select_for_update()
        .filter(pk__in=owners)
        .order_by('pk')
    )
    touched, withdrawn, severed = set(), 0, 0
    for fact in (
        MemoryFact.objects.select_for_update().filter(pk__in=identities).order_by('pk')
    ):
        if not fact.claims.filter(attachment_id=attachment_id).exists():
            continue
        touched.add(fact.owner_id)
        if fact.lifecycle_state == 'proposed':
            _forget_locked(fact, actor_id=None, reason='forget')
            withdrawn += 1
            continue
        from aichat.services.attachment_memory_journal import record

        severed_at = timezone.now()
        current_claims = fact.claims.filter(
            pk__in=selected_claims, attachment_id=attachment_id
        )
        for claim in current_claims:
            record(claim, fact=fact, severed_at=severed_at)
        _scrub_proposals(fact.pk)
        current_claims.update(
            attachment_id=None,
            source_thread=None,
            source_message=None,
            source_model='',
            source_object_id='',
            source_field='',
            severed_at=severed_at,
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
        severed += 1
    for owner_id in touched:
        _invalidate_summaries(owner_id)
    remaining = residual(attachment_id)
    if require_complete and remaining:
        raise ValueError('Attachment memory lineage changed during deletion; retry')
    return {
        'status': 'purge_incomplete' if remaining else 'purged',
        'withdrawn': withdrawn,
        'severed': severed,
        'remaining_claims': remaining,
    }


def before_attachment_delete(sender, instance, using, **kwargs):
    """Keep the source intact if its derivatives cannot be cleaned atomically."""
    if using != 'default':
        raise ValueError('Attachment memory deletion requires the primary database')
    cleanup(instance.pk, require_complete=True)
