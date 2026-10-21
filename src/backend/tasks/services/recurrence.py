"""O15 recurrence policy: exact component/time facts, explicit commands only."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q

from rest_framework.exceptions import ValidationError

from assets.health_models import MachineAnomaly, MachineSignalBinding
from tasks.models import WorkOrder
from tasks.scope import scoped_machines, scoped_work_orders


def days():
    """Return the configured observation window, never a physical criterion."""
    value = getattr(settings, 'AIMMS_RECURRENCE_DAYS', 30)
    if type(value) is not int or value < 1:
        raise ValidationError('Recurrence days must be a positive integer.')
    return value


def closure_time(work):
    """Retained closeout time survives clearing the live completion on reopen."""
    closeout = getattr(work, 'structured_closeout', None)
    return work.actual_completed_at or (closeout.completed_at if closeout else None)


def acceptance_state(work):
    """Only an enabled acceptance provider can establish awaiting verification."""
    closeout = getattr(work, 'structured_closeout', None)
    if (
        closeout
        and closeout.verified_by_id is not None
        and closeout.verified_at is not None
    ):
        return 'accepted'
    if closeout and (
        closeout.verified_by_id is not None or closeout.verified_at is not None
    ):
        return 'unknown'
    if str(getattr(settings, 'AIMMS_CLOSEOUT_VERIFY_POLICY', 'off')) not in (
        'optional',
        'supervisor',
    ):
        return 'unknown'
    return 'awaiting_verification' if closeout else 'unknown'


def decision(work, occurrence):
    """Unknown acceptance and retained provenance route to linked corrective work."""
    closed_at = closure_time(work)
    if not work.is_active or work.lifecycle_status == 'canceled':
        return 'ineligible'
    matches = bool(
        work.affected_component_ref
        and occurrence.machine_id == work.machine_id
        and occurrence.bindings.filter(
            dictionary_point__component__machine_id=work.machine_id,
            dictionary_point__component__code=work.affected_component_ref,
        ).exists()
    )
    within = bool(
        closed_at
        and closed_at
        < occurrence.first_observed_at
        <= closed_at + timedelta(days=days())
    )
    if not matches or not within or getattr(work, 'structured_closeout', None) is None:
        return 'ineligible'
    if work.lifecycle_status == 'completed':
        return (
            'reopen'
            if acceptance_state(work) == 'awaiting_verification'
            and not reopen_blocker(work)
            else 'create_linked'
        )
    if (
        occurrence.work_order_id == work.pk
        and work.events.filter(event_type='RECURRENCE_REOPENED').exists()
    ):
        return 'already_reopened'
    return 'unowned_during_rework'


def reopen_blocker(work):
    """Retain native packet ownership and dependent closure provenance."""
    if hasattr(work, 'repair_packet'):
        return 'packet_owns_reopen'
    closeout = getattr(work, 'structured_closeout', None)
    if closeout and (
        hasattr(closeout, 'source_capture')
        or closeout.effects.exists()
        or closeout.amendments.exists()
    ):
        return 'closure_revision_required'
    return None


def eligible_prior(*, actor, machine_id, occurrence_id, prior_id):
    """Reauthorize and lock the prior Work before the create command acts."""
    if not scoped_machines(actor).filter(pk=machine_id).exists():
        raise ValidationError('Recurrence machine is unavailable in this scope.')
    work = (
        scoped_work_orders(actor)
        .select_for_update(of=('self',))
        .select_related('structured_closeout')
        .filter(pk=prior_id, machine_id=machine_id, is_active=True)
        .first()
    )
    occurrence = (
        MachineAnomaly.objects
        .select_for_update()
        .filter(pk=occurrence_id, machine_id=machine_id)
        .first()
    )
    if (
        work is None
        or occurrence is None
        or occurrence.work_order_id is not None
        or occurrence.repair_packet_id is not None
        or decision(work, occurrence) not in ('create_linked', 'reopen')
    ):
        raise ValidationError(
            'No eligible same-component deviation within the closure window.'
        )
    return work, occurrence


def project(work, actor):
    """Read candidates without creating or reopening anything on page load."""
    from users.models import RuleSet

    window = days()
    closed_at = closure_time(work)
    if work.machine_id and (
        actor is None or not scoped_machines(actor).filter(pk=work.machine_id).exists()
    ):
        return {
            'rule': 'recurrence_policy.v1',
            'days': window,
            'available': False,
            'reason': 'machine_scope_unavailable',
            'items': [],
        }
    linked_from = None
    if (
        work.recurrence_of_id
        and actor
        and work.corrective_origin_kind != 'routine_finding'
    ):
        previous = scoped_work_orders(actor).filter(pk=work.recurrence_of_id).first()
        if previous:
            linked_from = {'id': previous.pk, 'reference': previous.reference}
    closeout = getattr(work, 'structured_closeout', None)
    if not work.is_active or work.lifecycle_status == 'canceled':
        return {
            'rule': 'recurrence_policy.v1',
            'days': window,
            'available': False,
            'reason': 'prior_work_inactive_or_canceled',
            'items': [],
            'linked_from': linked_from,
        }
    if (
        not work.machine_id
        or not work.affected_component_ref
        or not closed_at
        or closeout is None
    ):
        return {
            'rule': 'recurrence_policy.v1',
            'days': window,
            'available': False,
            'reason': 'component_or_closure_not_recorded',
            'items': [],
            'linked_from': linked_from,
        }
    if not MachineSignalBinding.objects.filter(
        machine_id=work.machine_id,
        dictionary_point__component__machine_id=work.machine_id,
        dictionary_point__component__code=work.affected_component_ref,
    ).exists():
        return {
            'rule': 'recurrence_policy.v1',
            'days': window,
            'available': False,
            'reason': 'component_not_bound',
            'items': [],
            'linked_from': linked_from,
        }
    owner_filter = Q(work_order__isnull=True, repair_packet__isnull=True) | Q(
        recurrence_work__recurrence_of=work
    )
    if work.lifecycle_status != 'completed':
        owner_filter |= Q(work_order=work)
    rows = (
        MachineAnomaly.objects
        .filter(
            machine_id=work.machine_id,
            first_observed_at__gt=closed_at,
            first_observed_at__lte=closed_at + timedelta(days=window),
            bindings__dictionary_point__component__machine_id=work.machine_id,
            bindings__dictionary_point__component__code=work.affected_component_ref,
        )
        .filter(owner_filter)
        .distinct()
        .order_by('first_observed_at', 'pk')[:25]
    )
    visible = scoped_work_orders(actor) if actor else WorkOrder.objects.none()
    items = []
    for row in rows:
        linked = visible.filter(recurrence_occurrence=row).first()
        if (
            (row.work_order_id is not None or row.repair_packet_id is not None)
            and linked is None
            and not (
                row.work_order_id == work.pk and work.lifecycle_status != 'completed'
            )
        ):
            continue
        action = decision(work, row)
        blocker = reopen_blocker(work) if action == 'reopen' else None
        items.append({
            'occurrence_id': row.pk,
            'title': row.title,
            'observed_at': row.first_observed_at.isoformat(),
            'component_ref': work.affected_component_ref,
            'action': 'linked'
            if linked
            else 'reopen_unavailable'
            if blocker
            else action,
            'unavailable_reason': blocker,
            'closure_acceptance': acceptance_state(work),
            'reopen_blocked_reason': reopen_blocker(work),
            'work_order_id': linked.pk if linked else None,
        })
    return {
        'rule': 'recurrence_policy.v1',
        'days': window,
        'available': True,
        'items': items,
        'linked_from': linked_from,
        'prior_work_id': work.pk,
        'prior_version': work.lifecycle_version,
        'closure_acceptance': acceptance_state(work),
        'can_reopen': bool(
            actor
            and actor.is_active
            and actor.has_perm('tasks.execute_workorder')
            and (
                actor.is_superuser
                or RuleSet.objects.filter(
                    group__user=actor, name='work_order', can_change=True, can_view=True
                ).exists()
            )
        ),
        'can_create': bool(
            actor
            and actor.is_authenticated
            and (
                actor.is_superuser
                or RuleSet.objects.filter(
                    group__user=actor, name='work_order', can_add=True, can_view=True
                ).exists()
            )
        ),
    }
