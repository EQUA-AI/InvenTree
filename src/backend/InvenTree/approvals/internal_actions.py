"""Screen-only E19 contracts: one named workflow or an in-app notification.

No arbitrary workflow runner, URL, script, email channel or actor identifier is
accepted. ApprovalExecution owns transaction and idempotency for both effects.
"""

from django.contrib.auth import get_user_model


def _actor(actor):
    return get_user_model().objects.get(pk=actor.pk, is_active=True)


def prepare_workflow(payload, *, actor=None):
    """The only registered workflow is one reviewed work-order plan update."""
    from tasks.models import WorkOrder

    from aichat.services.proposals import _authorized_work_order, _require_role

    if not isinstance(payload, dict) or set(payload) - {
        'workflow',
        'work_order_id',
        'fields',
        'snapshot',
    }:
        raise ValueError('Unsupported workflow fields')
    if payload.get('workflow') != 'update_work_order_plan':
        raise ValueError('Only the update_work_order_plan workflow is registered')
    if actor is not None:
        actor = _actor(actor)
        _require_role(actor, 'work_order.update')
        work_order = _authorized_work_order(actor, payload.get('work_order_id'))
    else:
        work_order = WorkOrder.objects.get(pk=payload.get('work_order_id'))
    fields = payload.get('fields')
    if (
        not isinstance(fields, dict)
        or not fields
        or set(fields) - {'title', 'description', 'priority'}
    ):
        raise ValueError('Review only title, description or priority changes')
    if any(not isinstance(value, str) for value in fields.values()):
        raise ValueError('Workflow field values must be explicit text')
    return {
        'workflow': 'update_work_order_plan',
        'work_order_id': work_order.pk,
        'fields': fields,
        'snapshot': {
            'reference': work_order.reference,
            'version': work_order.lifecycle_version,
            'status': work_order.lifecycle_status,
            'before': {key: getattr(work_order, key) for key in fields},
        },
    }


def execute_workflow(approval, actor, key):
    """Canonical optimistic-version command and its immutable audit receipt."""
    from tasks.models import WorkOrder
    from tasks.services.scheduling import update_work_order_plan

    from aichat.services.proposals import _command_receipt

    payload = approval.payload
    WorkOrder.objects.select_for_update().get(pk=payload['work_order_id'])
    if (
        prepare_workflow(payload, actor=actor) != payload
        or payload != approval.baseline_context
    ):
        raise ValueError('Workflow target changed after review')
    result = update_work_order_plan(
        work_order_id=payload['work_order_id'],
        actor=_actor(actor),
        expected_version=payload['snapshot']['version'],
        idempotency_key=key,
        fields=payload['fields'],
    )
    return {'workflow': 'update_work_order_plan', 'effects': [_command_receipt(result)]}


def notification_actor(actor):
    """Publishing a notification needs its own canonical Django model grant."""
    actor = _actor(actor)
    if not actor.has_perm('common.add_notificationmessage'):
        raise PermissionError('Permission to create in-app notifications is required')
    return actor


def prepare_notification(payload, *, actor=None):
    """Resolve every recipient; recipient text never grants publisher authority."""
    if actor is not None:
        notification_actor(actor)
    if not isinstance(payload, dict) or set(payload) - {
        'channel',
        'recipients',
        'recipient_details',
        'title',
        'message',
    }:
        raise ValueError('Unsupported notification fields')
    if payload.get('channel') != 'in_app':
        raise ValueError('Only the in_app channel is supported; email is unavailable')
    ids = payload.get('recipients')
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 20
        or any(type(pk) is not int or pk < 1 for pk in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError('Select one to 20 distinct user IDs')
    users = list(
        get_user_model().objects.filter(pk__in=ids, is_active=True).order_by('pk')
    )
    if len(users) != len(ids):
        raise ValueError('Every notification recipient must be active')
    for field in ('title', 'message'):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 250:
            raise ValueError(f'{field} must contain one to 250 characters')
    return {
        'channel': 'in_app',
        'recipients': [user.pk for user in users],
        'recipient_details': [
            {'id': user.pk, 'username': user.username, 'name': user.get_full_name()}
            for user in users
        ],
        'title': payload['title'],
        'message': payload['message'],
    }


def execute_notification(approval, actor, key):
    """Call only the canonical UI handler, never the multi-channel dispatcher."""
    from common.models import NotificationMessage
    from plugin.builtin.integration.core_notifications import InvenTreeUINotifications

    notification_actor(actor)
    payload = approval.payload
    users = list(
        get_user_model()
        .objects.select_for_update()
        .filter(pk__in=payload['recipients'])
        .order_by('pk')
    )
    if (
        prepare_notification(payload, actor=actor) != payload
        or payload != approval.baseline_context
    ):
        raise ValueError('Notification recipients changed after review')
    category = f'approval-in-app:{key}'
    handler = InvenTreeUINotifications()
    handler.send_notification(
        target=approval,
        category=category,
        users=users,
        context={'name': payload['title'], 'message': payload['message']},
    )
    rows = list(
        NotificationMessage.objects.filter(
            category=category, target_object_id=str(approval.pk)
        ).order_by('user_id')
    )
    if [row.user_id for row in rows] != payload['recipients'] or any(
        row.name != payload['title'] or row.message != payload['message']
        for row in rows
    ):
        raise ValueError('In-app notification receipt could not be verified')
    return {
        'channel': 'in_app',
        'notification_ids': [row.pk for row in rows],
        'recipient_ids': payload['recipients'],
        'recorded': True,
        'read': False,
        'email_sent': False,
    }


def visible(approval, actor):
    """Fresh permission/scope checks for assigned screen reviewers only."""
    try:
        if approval.action_type == 'workflow':
            prepare_workflow(approval.payload, actor=actor)
        else:
            prepare_notification(approval.payload, actor=actor)
        return True
    except Exception:
        return False
