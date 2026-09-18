"""Explicit local account-domain cleanup; shared correspondence is preserved.

These adapters do not revoke provider tokens remotely, delete another user's
mailbox, transfer shared ownership or infer historical client transcript scope.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q

from aichat.email_models import ConnectedMailbox, MailboxGrant, MailboxOAuthAttempt
from assets.models import ClientScopeGrant


def residuals(user_id):
    """Counts only; retained mailbox content is a separate offboarding obligation."""
    return {
        'client_grants': ClientScopeGrant.objects.filter(user_id=user_id).count(),
        'mailbox_grants': MailboxGrant.objects.filter(user_id=user_id).count(),
        'mailbox_oauth_attempts': MailboxOAuthAttempt.objects.filter(
            actor_id=user_id
        ).count(),
        'owned_mailbox_credentials_or_effects': ConnectedMailbox.objects
        .filter(owner_id=user_id)
        .filter(
            Q(enabled=True)
            | Q(send_enabled=True)
            | Q(receive_enabled=True)
            | ~Q(encrypted_credentials='')
        )
        .count(),
    }


@transaction.atomic
def purge(user_id, *, batch_size=200):
    """Stop owned mailbox effects and erase local secrets under an inactive owner."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError('Invalid account-domain batch')
    owner = get_user_model().objects.select_for_update().get(pk=user_id)
    if owner.is_active:
        raise ValueError('Account must be disabled before domain cleanup')
    counts = {}
    for name, queryset in (
        ('client_grants', ClientScopeGrant.objects.filter(user_id=user_id)),
        ('mailbox_grants', MailboxGrant.objects.filter(user_id=user_id)),
        (
            'mailbox_oauth_attempts',
            MailboxOAuthAttempt.objects.filter(actor_id=user_id),
        ),
    ):
        ids = list(queryset.order_by('pk').values_list('pk', flat=True)[:batch_size])
        counts[name] = queryset.filter(pk__in=ids).delete()[0]
    pending = ConnectedMailbox.objects.filter(owner_id=user_id).filter(
        Q(enabled=True)
        | Q(send_enabled=True)
        | Q(receive_enabled=True)
        | ~Q(encrypted_credentials='')
    )
    ids = list(
        pending
        .select_for_update()
        .order_by('pk')
        .values_list('pk', flat=True)[:batch_size]
    )
    counts['mailboxes_disconnected'] = pending.filter(pk__in=ids).update(
        enabled=False,
        send_enabled=False,
        receive_enabled=False,
        encrypted_credentials='',
        binding_version=F('binding_version') + 1,
        verified_send_at=None,
        verified_receive_at=None,
        oauth_refresh_until=None,
        health='disconnected',
    )
    remaining = residuals(user_id)
    return {
        'status': 'purge_incomplete' if any(remaining.values()) else 'purged',
        'processed': counts,
        'residuals': remaining,
        'retained_owned_mailboxes': ConnectedMailbox.objects.filter(
            owner_id=user_id
        ).count(),
        'shared_correspondence_erased': False,
        'upstream_revocation_verified': False,
    }
