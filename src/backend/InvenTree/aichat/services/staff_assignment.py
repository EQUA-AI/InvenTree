"""Server-owned personnel policy for text, voice and approved AI proposals."""

from django.core.exceptions import PermissionDenied

from aichat.services.proposals import ProposalError, ProposalPreviewChanged

REVIEW_REASONS = {
    'ASSIGNMENT_REQUIREMENTS_UNKNOWN': 'Assignment review required: trade requirements have not been confirmed by a human.',
    'ASSIGNMENT_NO_QUALIFIED_MATCH': 'Assignment review required: no qualified staff member matches the confirmed trades.',
    'ASSIGNMENT_AMBIGUOUS': 'Assignment review required: more than one qualified staff member matches.',
    'ASSIGNMENT_TRADE_MISMATCH': 'Assignment review required: the selected person does not cover the confirmed trades.',
}


class AssignmentReviewRequired(ProposalError):  # noqa: N818
    """An AI suggestion cannot safely choose or replace the current assignee."""

    def __init__(self, code, message=None):
        """Preserve the shared policy's stable review code."""
        self.code = code
        super().__init__(
            message or REVIEW_REASONS.get(code, 'Assignment review required.')
        )


def _decision(actor, work_order, intent, *, select=False):
    from tasks.services.staff_assignment import (
        AssignmentError,
        auto_assignment,
        validate_assignment,
    )

    required = list(work_order.required_trades or [])
    confirmed = bool(work_order.trade_requirements_confirmed)
    selected = intent.get('assigned_to')
    try:
        if intent.get('auto_assign'):
            decision = auto_assignment(
                actor,
                work_order,
                required_trades=required,
                requirements_confirmed=confirmed,
            )
            if decision['assigned_to'] is None:
                raise AssignmentReviewRequired(decision['code'])
            if not select and decision['assigned_to'] != selected:
                raise ProposalPreviewChanged(
                    'The qualified candidate changed; request a fresh preview.'
                )
            selected = decision['assigned_to']
        decision = validate_assignment(
            actor,
            work_order,
            selected,
            required_trades=required,
            requirements_confirmed=confirmed,
            override_reason='',
            allow_override=False,
        )
    except AssignmentError as exc:
        raise AssignmentReviewRequired(exc.code, str(exc)) from exc
    except PermissionDenied as exc:
        raise ProposalError('Staff assignment is not available to you.') from exc
    if selected is not None and decision['code'] != 'ASSIGNMENT_QUALIFIED':
        raise AssignmentReviewRequired(decision['code'])
    return selected, required, confirmed, decision


def prepare_assignment(actor, work_order, intent):
    """Freeze one selection using only the target's trusted requirement record."""
    normalized = dict(intent)
    normalized['auto_assign'] = bool(
        intent.get('auto_assign', 'assigned_to' not in intent)
    )
    selected, required, confirmed, _ = _decision(
        actor, work_order, normalized, select=True
    )
    normalized.update(
        assigned_to=selected,
        required_trades=required,
        trade_requirements_confirmed=confirmed,
    )
    for key in ('override_reason', 'allow_override', 'requirements_confirmed'):
        normalized.pop(key, None)
    return normalized


def assignment_preview(actor, work_order, intent):
    """Revalidate without silently switching to a different qualified person."""
    from tasks.ai_read import fence

    from users.staff_directory import directory_rows

    selected, required, confirmed, decision = _decision(actor, work_order, intent)

    def person(pk):
        if pk is None:
            return {'display_name': 'Unassigned', 'job_title': ''}
        page = directory_rows(actor, work_order_id=work_order.pk, user_id=pk, limit=1)
        if not page['results']:
            if pk == selected:
                raise AssignmentReviewRequired('ASSIGNMENT_PERSON_UNAVAILABLE')
            return {'display_name': 'Assigned', 'job_title': ''}
        row = page['results'][0]
        return {
            'display_name': fence(row['display_name'], limit=255),
            'job_title': fence(row['job_title'], limit=255),
        }

    current = person(work_order.assigned_to_id)
    proposed = person(selected)
    return {
        'current_assigned_to_id': work_order.assigned_to_id,
        'proposed_assigned_to_id': selected,
        'current_assigned_to_name': current['display_name'],
        'proposed_assigned_to_name': proposed['display_name'],
        'proposed_job_title': proposed['job_title'],
        'required_trades': required,
        'trade_requirements_confirmed': confirmed,
        'assignment_code': decision['code'],
        'assignment_rationale': (
            'Approved active trades cover the server-confirmed work requirements.'
            if selected is not None
            else 'The work order will be unassigned.'
        ),
    }


def prepare_repair_intent(intent, *, actor=None):
    """A model-created job has no human-confirmed occupational requirements.

    Even an explicit model-selected person remains unassigned. Approval of the
    repair is not a separate confirmation of trade requirements or an override.
    """
    normalized = dict(intent)
    raw_planning = intent.get('planning') or {}
    if not isinstance(raw_planning, dict):
        raise ProposalError('Work-package planning must be an object.')
    planning = dict(raw_planning)
    personnel_keys = {
        'assigned_to',
        'required_trades',
        'trade_requirements_confirmed',
        'auto_assign',
    }
    if actor is not None and (
        planning.get('assignee') or personnel_keys.intersection(planning)
    ):
        from django.contrib.auth import get_user_model

        current_actor = (
            get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
        )
        permitted = current_actor is not None and all(
            current_actor.has_perm(permission)
            for permission in (
                'users.view_staff_directory',
                'tasks.assign_workorder',
                'tasks.plan_workorder',
            )
        )
        if not permitted:
            for key in personnel_keys:
                planning.pop(key, None)
            if 'assignee' in planning:
                planning['assignee'] = ''
    requested = bool(
        planning.get('auto_assign')
        or planning.get('assigned_to') is not None
        or planning.get('assignee')
    )
    if requested or any(
        key in planning
        for key in (
            'assigned_to',
            'required_trades',
            'auto_assign',
            'trade_requirements_confirmed',
        )
    ):
        planning.update(
            assigned_to=None,
            assignee='',
            auto_assign=requested,
            trade_requirements_confirmed=False,
        )
    for key in ('override_reason', 'allow_override', 'requirements_confirmed'):
        planning.pop(key, None)
        normalized.pop(key, None)
    normalized['planning'] = planning
    normalized.pop('_staff_proposal_id', None)
    return normalized


def repair_assignment_preview():
    """Explain abstention without disclosing a model-nominated person's record."""
    return {
        'proposed_assigned_to_id': None,
        'proposed_assigned_to_name': 'Unassigned',
        'assignment_code': 'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
        'assignment_rationale': REVIEW_REASONS['ASSIGNMENT_REQUIREMENTS_UNKNOWN'],
    }


def visible_personnel_fields(proposal, actor):
    """Do not replay a stored occupational title after directory access is lost."""
    from django.http import Http404

    from users.staff_directory import directory_rows

    preview = dict(proposal.preview or {})
    receipt = dict(proposal.receipt or {})
    if proposal.action_type != 'work_order.assign' or not preview.get(
        'proposed_job_title'
    ):
        return preview, receipt
    try:
        page = directory_rows(
            actor,
            work_order_id=proposal.target_work_order_id,
            user_id=preview.get('proposed_assigned_to_id'),
            limit=1,
        )
        permitted = bool(page['results'])
    except (PermissionDenied, Http404):
        permitted = False
    if not permitted:
        preview.pop('proposed_job_title', None)
        if isinstance(receipt.get('assignment'), dict):
            receipt['assignment'] = dict(receipt['assignment'])
            receipt['assignment'].pop('proposed_job_title', None)
    return preview, receipt


def validate_approval_assignment(payload):
    """Recheck the linked chat preview; a payload flag never grants AI authority."""
    from django.contrib.auth import get_user_model

    from aichat.models import ChatActionProposal
    from aichat.services.proposals import (
        _authorize_machine_scope,
        _preview,
        _require_role,
        compute_preview_hash,
    )

    proposal = (
        ChatActionProposal.objects
        .filter(action_type='repair_work_package.create', approval__payload=payload)
        .select_related('approval')
        .first()
    )
    if proposal is None:
        if '_staff_proposal_id' not in payload:
            return
        raise ProposalError('The staff assignment approval binding is invalid.')
    expected = {
        **proposal.intent,
        'actor_id': proposal.owner_id,
        '_staff_proposal_id': str(proposal.pk),
    }
    if payload != expected:
        raise ProposalPreviewChanged('The reviewed staff assignment changed.')
    actor = (
        get_user_model().objects.filter(pk=proposal.owner_id, is_active=True).first()
    )
    if actor is None or payload.get('actor_id') != actor.pk:
        raise ProposalError('The requesting actor is no longer available.')
    sanitized = prepare_repair_intent(proposal.intent, actor=actor)
    if sanitized != proposal.intent:
        raise ProposalPreviewChanged('Review the unassigned repair draft again.')
    _require_role(actor, proposal.action_type)
    _authorize_machine_scope(actor, sanitized.get('machine_id'))
    if (
        compute_preview_hash(
            _preview(None, proposal.action_type, sanitized, owner=actor)
        )
        != proposal.preview_hash
    ):
        raise ProposalPreviewChanged(
            'The repair preview changed; request a fresh preview.'
        )
