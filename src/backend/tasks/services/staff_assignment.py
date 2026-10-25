"""Deterministic suitability checks; job titles never prove a qualification."""

from django.core.exceptions import PermissionDenied

from tasks.permissions import ASSIGN_WORKORDER, require_permission
from tasks.scope import scope_for_work_order
from tasks.services.work_orders import WorkOrderCommandError
from users.staff_directory import (
    executor_ids,
    require_directory,
    validate_trade_codes,
    visible_people,
)


class AssignmentError(WorkOrderCommandError):
    """An assignment cannot safely be applied."""

    def __init__(self, code, message):
        """Keep stable machine-readable decision codes."""
        self.code = code
        super().__init__(message)


def eligible_people(actor, work_order):
    """Return scoped personnel who actually hold work execution authority."""
    require_directory(actor)
    executors = executor_ids()
    return [
        person
        for person in visible_people(
            actor, target_scope=scope_for_work_order(work_order)
        )
        if person.is_active and person.profile.active and person.pk in executors
    ]


def validate_assignment(
    actor,
    work_order,
    assigned_to,
    *,
    required_trades,
    requirements_confirmed,
    override_reason='',
    allow_override=False,
):
    """Validate selected identity again at commit; only humans may override."""
    from django.contrib.auth.models import User

    require_directory(actor)
    current_actor = User.objects.get(pk=actor.pk)
    require_permission(current_actor, ASSIGN_WORKORDER)
    if not isinstance(requirements_confirmed, bool) or not isinstance(
        override_reason, str
    ):
        raise AssignmentError(
            'ASSIGNMENT_REQUIREMENTS_INVALID',
            'Confirmation must be a boolean and override reason must be text.',
        )
    try:
        required = set(validate_trade_codes(required_trades))
    except ValueError as exc:
        raise AssignmentError('ASSIGNMENT_REQUIREMENTS_INVALID', str(exc)) from exc
    if assigned_to is None:
        return {'code': 'ASSIGNMENT_UNASSIGNED'}
    person = next(
        (p for p in eligible_people(actor, work_order) if p.pk == assigned_to), None
    )
    if person is None:
        raise AssignmentError(
            'ASSIGNMENT_PERSON_UNAVAILABLE',
            'The selected person is inactive, out of scope, or cannot execute work.',
        )
    approved = {trade.code for trade in person.profile.trades.all() if trade.active}
    if requirements_confirmed and required and not required.issubset(approved):
        if not allow_override or not override_reason.strip():
            raise AssignmentError(
                'ASSIGNMENT_TRADE_MISMATCH',
                'The selected person does not cover the required trades. A supervisor override with a reason is required.',
            )
        if not current_actor.has_perm('tasks.override_assignment_trade_mismatch'):
            raise PermissionDenied('Assignment trade override permission is required.')
        return {
            'code': 'ASSIGNMENT_MANUAL_OVERRIDE',
            'override_reason': override_reason.strip(),
            'approved_trades': sorted(approved),
        }
    return {
        'code': 'ASSIGNMENT_QUALIFIED'
        if required and requirements_confirmed
        else 'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
        'approved_trades': sorted(approved),
    }


def auto_assignment(actor, work_order, *, required_trades, requirements_confirmed):
    """Choose exactly one fully qualified candidate or explicitly abstain."""
    from django.contrib.auth.models import User

    require_directory(actor)
    require_permission(User.objects.get(pk=actor.pk), ASSIGN_WORKORDER)
    if not isinstance(requirements_confirmed, bool):
        raise AssignmentError(
            'ASSIGNMENT_REQUIREMENTS_INVALID', 'Confirmation must be a boolean.'
        )
    try:
        required = set(validate_trade_codes(required_trades))
    except ValueError as exc:
        raise AssignmentError('ASSIGNMENT_REQUIREMENTS_INVALID', str(exc)) from exc
    if not requirements_confirmed or not required:
        return {
            'assigned_to': None,
            'code': 'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
            'candidates': [],
        }
    people = eligible_people(actor, work_order)
    matches = [
        p.pk
        for p in people
        if required.issubset({t.code for t in p.profile.trades.all() if t.active})
    ]
    return {
        'assigned_to': matches[0] if len(matches) == 1 else None,
        'code': 'ASSIGNMENT_QUALIFIED'
        if len(matches) == 1
        else ('ASSIGNMENT_AMBIGUOUS' if matches else 'ASSIGNMENT_NO_QUALIFIED_MATCH'),
        'candidates': matches,
    }
