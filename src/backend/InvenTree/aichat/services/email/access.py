"""Fresh user and mailbox checks shared by HTTP, tools, and workers."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q

from ai.core.integrations.email.contracts import MailboxError
from aichat.models import ConnectedMailbox, MailboxGrant


def require_enabled():
    """The new correspondence path is explicitly opt-in."""
    if not getattr(settings, 'AGENT_EMAIL_ENABLED', False):
        raise MailboxError('feature_disabled')


def current_user(actor):
    """Discard cached permissions and reject deactivated users."""
    user = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    if user is None:
        raise MailboxError('permission_denied')
    return user


def account_ids(actor, action='read'):
    """Return only accounts granted to this live user or their current groups."""
    user = current_user(actor)
    if action not in ('read', 'draft', 'send', 'admin'):
        raise MailboxError('permission_denied')
    if user.is_superuser:
        return ConnectedMailbox.objects.values_list('pk', flat=True)
    permission = (
        'aichat.change_connectedmailbox' if action == 'admin' else 'users.view_email'
    )
    if not user.has_perm(permission) or (
        action == 'send' and not user.has_perm('users.send_email')
    ):
        return ConnectedMailbox.objects.none().values_list('pk', flat=True)
    grants = MailboxGrant.objects.filter(Q(user=user) | Q(group__in=user.groups.all()))
    allowed = grants.filter(**{f'can_{action}': True}).values_list(
        'account_id', flat=True
    )
    if action in ('draft', 'send'):
        readers = grants.filter(can_read=True).values_list('account_id', flat=True)
        return (
            ConnectedMailbox.objects
            .filter(pk__in=allowed)
            .filter(pk__in=readers)
            .values_list('pk', flat=True)
        )
    return allowed


def require_account(actor, account_id, action='read'):
    """Hide nonexistent and unauthorized accounts behind the same error."""
    try:
        account = ConnectedMailbox.objects.filter(
            pk=account_id, pk__in=account_ids(actor, action)
        ).first()
    except (ValueError, TypeError, ValidationError):
        account = None
    if account is None:
        raise MailboxError('permission_denied')
    return account


def visible_approvals(queryset, actor):
    """Account-private drafts stay private even when feature enablement stops."""
    try:
        user = current_user(actor)
    except MailboxError:
        return queryset.none()
    if user.is_superuser:
        return queryset
    unbound = Q(email_draft__isnull=True)
    if getattr(settings, 'AGENT_EMAIL_ENABLED', False):
        unbound &= ~Q(action_type='email')
    return queryset.filter(unbound | Q(email_draft__account_id__in=account_ids(user)))


def require_approval(actor, approval):
    """Apply the same object scope for internal and voice approval calls."""
    from approvals.models import Approval

    if not visible_approvals(Approval.objects.filter(pk=approval.pk), actor).exists():
        raise MailboxError('permission_denied')
