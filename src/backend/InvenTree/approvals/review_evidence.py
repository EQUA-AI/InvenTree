"""Server-owned review evidence: exact content, actor, scope and delivery."""

import os
import textwrap

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ApprovalReviewAcknowledgment, ApprovalReviewDelivery, EventType
from .policy import effective_risk_tier
from .review_sections import build_review_sections, compute_review_hash


class ReviewEvidenceError(Exception):
    """A review is absent, incomplete, stale or belongs to a different actor."""


def revision_binding_enabled():
    """Default-off screen rollout flag; voice never uses the legacy shortcut."""
    value = getattr(settings, 'APPROVAL_REVIEW_REVISION_BOUND', None)
    if value is None:
        value = os.environ.get('APPROVAL_REVIEW_REVISION_BOUND', 'false')
    return value is True or str(value).lower() in ('1', 'true', 'yes')


def _scope_hash(actor, approval=None):
    if approval is not None and hasattr(approval, 'email_draft'):
        from ai.core.integrations.email.contracts import MailboxError
        from aichat.services.email.access import require_account
        from aichat.services.email.drafts import digest

        try:
            account = require_account(actor, approval.email_draft.account_id)
        except MailboxError as exc:
            raise ReviewEvidenceError('Mailbox review access is unavailable.') from exc
        return digest({
            'mailbox': str(account.pk),
            'binding_version': account.binding_version,
            'actor': actor.pk,
        })
    from aichat.services.proposals import ProposalError
    from aichat.services.scope_strings import scope_strings

    try:
        return scope_strings(actor)[1]
    except ProposalError as exc:
        raise ReviewEvidenceError(
            'The current review scope cannot be resolved.'
        ) from exc


def required_sections(approval):
    """Required top-level section identifiers for explicit acknowledgment."""
    return [s['id'] for s in build_review_sections(approval) if s['required']]


def review_units(approval):
    """Bounded spoken pages covering every required section, including full body."""
    units = []
    for section in build_review_sections(approval):
        if not section['required']:
            continue
        chunks = textwrap.wrap(
            section['text'], width=550, replace_whitespace=False
        ) or ['']
        for index, chunk in enumerate(chunks, 1):
            units.append({
                'id': f'{section["id"]}:{index}/{len(chunks)}',
                'text': f'{section["label"]}, part {index} of {len(chunks)}. {chunk}',
            })
    return units


@transaction.atomic
def record_delivery(approval, *, actor, utterance, unit_ids):
    """Bind a server-created utterance to exactly the review pages it contains.

    This is an internal hook at persist-before-speak, not a public endpoint.
    Recording a mapping does not claim playback completion.
    """
    scope_hash = _scope_hash(actor)
    if (
        utterance.session.owner_id != actor.pk
        or utterance.session.scope_hash != scope_hash
    ):
        raise ReviewEvidenceError(
            'Review delivery belongs to a different actor or scope.'
        )
    pages = {unit['id']: unit['text'] for unit in review_units(approval)}
    if (
        not unit_ids
        or len(set(unit_ids)) != len(unit_ids)
        or any(u not in pages for u in unit_ids)
    ):
        raise ReviewEvidenceError('Unknown or repeated review page.')
    expected = ' '.join(pages[u] for u in unit_ids)
    import hashlib

    if (
        utterance.spoken_summary != expected
        or utterance.spoken_summary_hash
        != hashlib.sha256(expected.encode()).hexdigest()
    ):
        raise ReviewEvidenceError(
            'The persisted utterance does not contain the exact review pages.'
        )
    for unit_id in unit_ids:
        _, created = ApprovalReviewDelivery.objects.get_or_create(
            utterance=utterance,
            section_id=unit_id,
            defaults={
                'approval': approval,
                'actor': actor,
                'revision': approval.current_revision_number,
                'review_hash': compute_review_hash(approval),
                'scope_hash': scope_hash,
            },
        )
        if (
            not created
            and not ApprovalReviewDelivery.objects.filter(
                utterance=utterance,
                section_id=unit_id,
                approval=approval,
                actor=actor,
                revision=approval.current_revision_number,
                review_hash=compute_review_hash(approval),
                scope_hash=scope_hash,
            ).exists()
        ):
            raise ReviewEvidenceError(
                'This utterance is already bound to another review.'
            )


def acknowledge(approval, *, actor, channel, evidence):
    """Persist an explicit acknowledgment after authoritative evidence checks."""
    if channel not in ('screen', 'voice') or not isinstance(evidence, dict):
        raise ReviewEvidenceError('Explicit revision and review evidence are required.')
    current_hash = compute_review_hash(approval)
    required = required_sections(approval)
    if (
        type(evidence.get('revision')) is not int
        or evidence['revision'] != approval.current_revision_number
        or evidence.get('review_hash') != current_hash
        or evidence.get('actor_id', actor.pk) != actor.pk
        or evidence.get('sections') != required
    ):
        raise ReviewEvidenceError(
            'Review evidence does not match this actor and current request.'
        )
    binding = {
        'approval': approval,
        'actor': actor,
        'revision': approval.current_revision_number,
        'review_hash': current_hash,
        'scope_hash': _scope_hash(actor, approval if channel == 'screen' else None),
    }
    if channel == 'voice':
        if evidence.get('acknowledgment') != 'I have reviewed this request':
            raise ReviewEvidenceError('An explicit review acknowledgment is required.')
        from voice.models import PlaybackState

        delivered = ApprovalReviewDelivery.objects.filter(
            **binding,
            utterance__playback_state=PlaybackState.DONE,
            utterance__session__owner=actor,
            utterance__session__scope_hash=binding['scope_hash'],
        )
        latest_invalidation = (
            approval.review_acknowledgments
            .filter(invalidated_at__isnull=False)
            .order_by('-invalidated_at')
            .values_list('invalidated_at', flat=True)
            .first()
        )
        failed_check = (
            approval.events
            .filter(event_type=EventType.REVALIDATION_FAILED)
            .order_by('-timestamp')
            .values_list('timestamp', flat=True)
            .first()
        )
        if failed_check and (
            not latest_invalidation or failed_check > latest_invalidation
        ):
            latest_invalidation = failed_check
        if latest_invalidation:
            delivered = delivered.filter(utterance__created_at__gt=latest_invalidation)
        if not {u['id'] for u in review_units(approval)}.issubset(
            set(delivered.values_list('section_id', flat=True))
        ):
            raise ReviewEvidenceError(
                'Every required review page must finish playing first.'
            )
    acknowledgment, _ = ApprovalReviewAcknowledgment.objects.update_or_create(
        **binding,
        defaults={
            'sections': required,
            'channel': channel,
            'invalidated_at': None,
            'created_at': timezone.now(),
        },
    )
    return acknowledgment


def require_acknowledgment(approval, *, actor, channel):
    """Never allow a timestamp, another actor or an obsolete revision to approve."""
    if (
        channel == 'screen'
        and not revision_binding_enabled()
        and not hasattr(approval, 'email_draft')
    ):
        if approval.risk_tier < 2 or approval.viewed_confirmed_at:
            return
        raise ReviewEvidenceError(
            'Tier 2-3 approvals require confirm-viewed before approve'
        )
    if effective_risk_tier(approval) < 2:
        return
    if not ApprovalReviewAcknowledgment.objects.filter(
        approval=approval,
        actor=actor,
        revision=approval.current_revision_number,
        review_hash=compute_review_hash(approval),
        scope_hash=_scope_hash(actor, approval if channel == 'screen' else None),
        sections=required_sections(approval),
        invalidated_at__isnull=True,
    ).exists():
        raise ReviewEvidenceError(
            'Confirm review of this exact revision before approval.'
        )


def invalidate(approval):
    """Revision changes and failed drift checks invalidate all prior evidence."""
    approval.review_acknowledgments.filter(invalidated_at__isnull=True).update(
        invalidated_at=timezone.now()
    )
    if revision_binding_enabled() or hasattr(approval, 'email_draft'):
        approval.viewed_confirmed_at = None
        approval.viewed_confirmed_by_user = None
        approval.save(
            update_fields=[
                'viewed_confirmed_at',
                'viewed_confirmed_by_user',
                'updated_at',
            ]
        )
