"""O21: routine findings reuse explicit linked-corrective creation, never reopen."""

from rest_framework.exceptions import ValidationError

from tasks.models import WorkOrder, WorkOrderStepExecution
from tasks.scope import scoped_machines, scoped_work_orders
from tasks.services.work_orders import _require_version
from tasks.work_execution.routine import ROUTINE_TYPES

REFUSAL = 'Linked corrective Work is unavailable.'


def require_creator(actor):
    """Match the native Work add/view roles even for service callers."""
    from django.core.exceptions import PermissionDenied

    from users.models import RuleSet

    if (
        not actor
        or not actor.is_active
        or not (
            actor.is_superuser
            or RuleSet.objects.filter(
                group__user=actor, name='work_order', can_add=True, can_view=True
            ).exists()
        )
    ):
        raise PermissionDenied('Work creation permission required.')


def source(*, actor, machine_id, planning, replay=False):
    """Lock and authorize the exact routine Work and optional record identities."""
    require_creator(actor)
    for key in ('recurrence_of', 'prior_version'):
        if type(planning.get(key)) is not int or planning[key] < 1:
            raise ValidationError(REFUSAL)
    # Lock the source by its durable identity for replay. Its current machine
    # may differ from the original intent; only a new create binds that pair.
    candidates = visible_work(actor).select_for_update(of=('self',))
    if not replay:
        candidates = candidates.filter(machine_id=machine_id)
    work = candidates.filter(pk=planning['recurrence_of']).first()
    if work is None:
        raise ValidationError(REFUSAL)
    if replay:
        return work
    if not scoped_machines(actor).filter(pk=machine_id).exists():
        raise ValidationError(REFUSAL)
    if (
        work.work_order_type not in ROUTINE_TYPES
        or not work.is_active
        or work.lifecycle_status == 'canceled'
    ):
        raise ValidationError(REFUSAL)
    _require_version(work, planning['prior_version'])
    if (
        planning.get('recurrence_occurrence') is not None
        or planning.get('work_order_type', 'corrective') != 'corrective'
        or not isinstance(planning.get('corrective_finding'), str)
        or not planning['corrective_finding'].strip()
    ):
        raise ValidationError(REFUSAL)
    for key in ('corrective_reading', 'corrective_step'):
        value = planning.get(key)
        if value is not None and (type(value) is not int or value < 1):
            raise ValidationError(REFUSAL)
    reading = planning.get('corrective_reading')
    step = planning.get('corrective_step')
    if reading and not work.closeout_readings.filter(pk=reading).exists():
        raise ValidationError(REFUSAL)
    if (
        step
        and not WorkOrderStepExecution.objects.filter(
            pk=step, application__work_order=work
        ).exists()
    ):
        raise ValidationError(REFUSAL)
    if (
        reading
        and step
        and not work.closeout_readings.filter(
            pk=reading, step_execution_id=step
        ).exists()
    ):
        raise ValidationError(REFUSAL)
    return work


def visible_work(actor):
    """A scope grant does not replace the Work read role."""
    from users.permissions import check_user_role

    if (
        not actor
        or not actor.is_active
        or not check_user_role(actor, 'work_order', 'view')
    ):
        return WorkOrder.objects.none()
    return scoped_work_orders(actor)


def linked(work, actor):
    """Do not disclose counts, identities or observations of unreadable children."""
    return [
        {
            'id': child.pk,
            'reference': child.reference,
            'title': child.title,
            'finding': child.corrective_finding,
            'reading_id': child.corrective_reading_id,
            'step_id': child.corrective_step_id,
        }
        for child in visible_work(actor)
        .filter(recurrence_of=work, corrective_origin_kind='routine_finding')
        .order_by('pk')
    ]


def origin(work, actor):
    """The corrective overview names its routine source only if still readable."""
    if work.corrective_origin_kind != 'routine_finding':
        return None
    previous = visible_work(actor).filter(pk=work.recurrence_of_id).first()
    if previous is None:
        return {'state': 'unavailable'}
    return {
        'state': 'available',
        'id': previous.pk,
        'reference': previous.reference,
        'finding': work.corrective_finding,
        'reading_id': work.corrective_reading_id,
        'step_id': work.corrective_step_id,
    }


def creation_availability(work, actor):
    """Explain a read-only hint without presenting lifecycle as authorization."""
    from django.core.exceptions import PermissionDenied

    reason = None
    try:
        require_creator(actor)
    except PermissionDenied:
        reason = 'permission'
    else:
        if not work.is_active or work.lifecycle_status == 'canceled':
            reason = 'routine_inactive_or_canceled'
        elif not visible_work(actor).filter(pk=work.pk).exists():
            reason = 'routine_scope'
        elif not scoped_machines(actor).filter(pk=work.machine_id).exists():
            reason = 'machine_scope'
    return {'can_create_linked': reason is None, 'create_unavailable_reason': reason}
