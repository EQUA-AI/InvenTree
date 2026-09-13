"""Assigned-reviewer discovery shared by REST and voice, with explicit scope maps."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q


def scoped_inbox_enabled():
    """Keep the legacy screen rollout separate; voice always requires this gate."""
    value = getattr(
        settings,
        'APPROVAL_INBOX_SCOPED',
        os.environ.get('APPROVAL_INBOX_SCOPED', 'false'),
    )
    return value is True or str(value).lower() in ('true', '1', 'yes')


def _json_ids(path, values):
    # JSON identifiers have historically been both integers and decimal strings.
    ids = list(values)
    return Q(**{f'{path}__in': ids + [str(pk) for pk in ids]})


def has_purchase_view(user):
    """Read current group roles, without a stale per-request permission cache."""
    return (
        user.is_superuser
        or user.groups.filter(
            rule_sets__name='purchase_order', rule_sets__can_view=True
        ).exists()
    )


def visible_approvals(queryset, actor, *, force_scoped=False):
    """Resolve current role/assignment/record scope without widening mailbox ACLs.

    Purchasing's existing business boundary is its role, not supplier-as-tenant.
    Repairs inherit their actual machine's client. Other non-email action types
    remain hidden until their record-to-scope mapping is implemented. No payload
    ``actor_id``, free-text site, or asserted client id grants visibility.
    """
    from aichat.services.email.access import visible_approvals as mailbox_visible

    user = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    if user is None:
        return queryset.none()
    queryset = mailbox_visible(queryset, user)
    if not force_scoped and not scoped_inbox_enabled():
        return queryset
    # Email is a separately managed, mailbox-scoped workflow. Phase C voice
    # excludes it; the owner's new screen-mailbox implementation stays intact.
    emails = Q(action_type='email') if not force_scoped else Q(pk__in=[])
    if not user.has_perm('approvals.review'):
        return queryset.filter(emails)
    allowed = Q(pk__in=[])
    from .inventory import visible as stock_visible

    stock_ids = [
        approval.pk
        for approval in queryset.filter(
            action_type='stock_update', assigned_to_user=user
        ).iterator()
        if stock_visible(approval, user)
    ]
    allowed |= Q(action_type='stock_update', pk__in=stock_ids)
    from .sales import visible as sales_visible

    sales_ids = [
        approval.pk
        for approval in queryset.filter(
            action_type='sales_order', assigned_to_user=user
        ).iterator()
        if sales_visible(approval, user)
    ]
    allowed |= Q(action_type='sales_order', pk__in=sales_ids)
    from .internal_actions import visible as internal_visible

    internal_ids = [
        approval.pk
        for approval in queryset.filter(
            action_type__in=['workflow', 'notification'], assigned_to_user=user
        ).iterator()
        if internal_visible(approval, user)
    ]
    allowed |= Q(pk__in=internal_ids)
    if has_purchase_view(user):
        from company.models import Company
        from order.models import PurchaseOrder

        allowed |= Q(action_type='purchase_order') & (
            _json_ids(
                'payload__supplier_id',
                Company.objects.filter(is_supplier=True).values_list('pk', flat=True),
            )
            | _json_ids(
                'payload__order_id', PurchaseOrder.objects.values_list('pk', flat=True)
            )
        )
    try:
        from tasks.scope import ScopeError, machine_scope_filter

        from assets.models import AssetMachine

        machines = AssetMachine.objects.filter(machine_scope_filter(user)).values_list(
            'pk', flat=True
        )
        allowed |= Q(action_type='repair_work_package') & _json_ids(
            'payload__machine_id', machines
        )
    except ScopeError:
        pass
    return queryset.filter(emails | (Q(assigned_to_user=user) & allowed))
