"""Governed action proposals (WS7).

The only executable actions are the verified allow-list builders below.
Creation re-reads the work order server-side and snapshots its version;
confirmation reauthorizes under a row lock and dispatches the canonical
work-order command service — never an AI tool — recording the real receipt
exactly once. Speech, transcripts, and model output are quoted, untrusted
display data throughout.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aichat.models import ChatActionProposal, ProposalAction, ProposalState

#: Adopted default (plan, 2026-07-15): 15 minutes, no extension.
PROPOSAL_EXPIRY_SECONDS = 15 * 60

_SAFETY_LINE = 'This does not change any safety status.'


class ProposalError(Exception):
    """Base class carrying a stable error code."""

    code = 'PROPOSAL_INVALID'


class CapabilityDenied(ProposalError):  # noqa: N818
    """The requested action is not on the executable allow-list."""

    code = 'CAPABILITY_DENIED'


class ProposalNotFound(ProposalError):  # noqa: N818
    """Unknown proposal, or one the actor does not own."""

    code = 'PROPOSAL_NOT_FOUND'


class ProposalExpired(ProposalError):  # noqa: N818
    """The proposal aged out before confirmation."""

    code = 'PROPOSAL_EXPIRED'


class ProposalStateConflict(ProposalError):  # noqa: N818
    """The proposal is already terminal."""

    code = 'PROPOSAL_STATE_CONFLICT'


class ProposalRevalidationFailed(ProposalError):  # noqa: N818
    """The target changed between proposal and confirmation."""

    code = 'PROPOSAL_REVALIDATION_FAILED'


class ApprovalOwnsExecution(ProposalError):  # noqa: N818
    """The linked global approval, not this rail, executes this proposal.

    Raised so a bridged proposal can never dual-execute: chat holds the
    contextual preview, the approval queue holds the authority.
    """

    code = 'APPROVAL_OWNS_EXECUTION'


class StrictConfirmationRequired(ProposalError):  # noqa: N818
    """An irreversible action needs its exact strict confirm phrase."""

    code = 'STRICT_CONFIRMATION_REQUIRED'


#: Irreversible actions require the actor to type/speak an exact phrase — the
#: irreversibility tier the voice rail always had, now enforced on the text rail
#: too (§5.3 point 3 / §6.1). The phrase is surfaced in the preview so the UI
#: knows what to ask; voice reuses the same map so both rails demand one control.
IRREVERSIBLE_CONFIRM_PHRASE: dict[str, str] = {
    ProposalAction.WORK_ORDER_DELETE.value: 'confirm delete'
}


def _require_strict_phrase(action_type: str, confirm_phrase: str) -> None:
    """Enforce the strict confirm phrase for an irreversible action."""
    required = IRREVERSIBLE_CONFIRM_PHRASE.get(action_type)
    if required is None:
        return
    if (confirm_phrase or '').strip().casefold() != required.casefold():
        raise StrictConfirmationRequired(
            f'this action requires the exact phrase "{required}"'
        )


def _work_order(work_order_id: int):
    from tasks.models import KanbanCard

    try:
        work_order = KanbanCard.objects.get(pk=work_order_id)
    except KanbanCard.DoesNotExist as exc:
        raise ProposalNotFound('no such work order') from exc
    return work_order


def _authorized_work_order(owner, work_order_id: int):
    from tasks.scope import ScopeError, require_work_order_scope

    work_order = _work_order(work_order_id)
    try:
        require_work_order_scope(owner, work_order)
    except ScopeError as exc:
        raise ProposalNotFound('no such work order') from exc
    return work_order


#: The board (Board/Calendar/Timeline) gates its scheduling-family endpoints on
#: the ``work_order`` RBAC ruleset. The proposal rail dispatches those same
#: commands directly, so it must enforce the same role — otherwise a read-only
#: actor could mutate by asking (§5.13 permission parity). Lifecycle actions
#: (hold/resume/assign/cancel/transition) are dispatched to
#: ``tasks.services.work_orders``, which self-enforces its Django permissions, so
#: they are intentionally absent here (double-checking a different permission
#: model would be wrong, not safer).
_REQUIRED_ROLE: dict[str, tuple[str, str]] = {
    ProposalAction.WORK_ORDER_SCHEDULE.value: ('work_order', 'change'),
    ProposalAction.WORK_ORDER_RESIZE.value: ('work_order', 'change'),
    ProposalAction.WORK_ORDER_UPDATE.value: ('work_order', 'change'),
    ProposalAction.WORK_ORDER_DELETE.value: ('work_order', 'delete'),
    ProposalAction.WORK_ORDER_CREATE.value: ('work_order', 'add'),
    ProposalAction.WORK_ORDER_CREATE_CHILD.value: ('work_order', 'add'),
    ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value: ('work_order', 'add'),
    ProposalAction.WORK_ORDER_GENERATE_PROCUREMENT.value: ('work_order', 'add'),
    ProposalAction.DEPENDENCY_CREATE.value: ('work_order', 'change'),
    ProposalAction.DEPENDENCY_DELETE.value: ('work_order', 'change'),
    ProposalAction.SCHEDULE_OPTIMIZE.value: ('work_order', 'change'),
}

#: Actions that create rather than target an existing card: no ``target`` and no
#: ``expected_version`` — authorization comes from the intent, not a pinned card.
_TARGETLESS_ACTIONS = frozenset({
    ProposalAction.WORK_ORDER_CREATE.value,
    ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value,
    ProposalAction.SCHEDULE_OPTIMIZE.value,
})


def _require_role(owner, action_type: str) -> None:
    """Enforce the same RBAC role the UI endpoint requires (permission parity)."""
    role = _REQUIRED_ROLE.get(action_type)
    if role is None:
        return
    from users.permissions import check_user_role

    if not check_user_role(owner, role[0], role[1]):
        raise CapabilityDenied(f'{action_type} requires {role[0]}.{role[1]}')


def _optimize_candidate_ids(intent: dict[str, Any]) -> list[int]:
    return [int(cid) for cid in (intent.get('candidate_ids') or [])]


def _authorize_and_bind(owner, action_type: str, work_order_id, intent):
    """Authorize the actor for the action's target(s); return the binding.

    Returns ``(target_work_order_id, target_version, preview_work_order)``. For
    creating actions the target is ``None``; scope is checked against the
    machine (create) or every candidate card (optimize) named in the intent.
    """
    if action_type in {
        ProposalAction.WORK_ORDER_CREATE.value,
        ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value,
    }:
        _authorize_machine_scope(owner, intent.get('machine_id'))
        return None, None, None
    if action_type == ProposalAction.SCHEDULE_OPTIMIZE.value:
        candidates = _optimize_candidate_ids(intent)
        if not candidates:
            raise ProposalError('optimize requires at least one candidate')
        for candidate_id in candidates:
            _authorized_work_order(owner, candidate_id)
        return None, None, None
    if action_type == ProposalAction.DEPENDENCY_DELETE.value:
        successor = _dependency_successor(owner, intent.get('dependency_id'))
        return successor.pk, successor.lifecycle_version, successor
    if action_type == ProposalAction.DEPENDENCY_CREATE.value:
        to_card = _authorized_work_order(owner, int(work_order_id))
        # Both endpoints of a new edge must be in the actor's scope.
        _authorized_work_order(owner, int(intent.get('from_card_id')))
        return to_card.pk, to_card.lifecycle_version, to_card
    work_order = _authorized_work_order(owner, int(work_order_id))
    return work_order.pk, work_order.lifecycle_version, work_order


def _authorize_machine_scope(owner, machine_id) -> None:
    """Authorize creation against the machine's own scope identity.

    A machine is reachable through its sales customer *or*, for internal plant
    assets, through the client that owns it. A machine with neither identity is
    still refused: that is an unscoped record, and guessing a boundary for it
    would be worse than declining.

    Failures are reported as "no such machine" rather than distinguishing "exists
    but not yours", so a caller cannot enumerate other tenants' assets.
    """
    from tasks.scope import MaintenanceScope, ScopeError, scope_for_actor

    from assets.models import AssetMachine

    if not machine_id:
        raise ProposalError('create requires a machine')
    machine = AssetMachine.objects.filter(pk=machine_id).first()
    if machine is None:
        raise ProposalNotFound('no such machine')

    if machine.customer_id is not None:
        required = MaintenanceScope(customer_id=machine.customer_id, site_key=None)
    elif machine.client_id is not None:
        required = MaintenanceScope(
            customer_id=None, site_key=None, client_id=machine.client_id
        )
    else:
        raise ProposalError(
            'machine has neither a customer nor a client, so it has no scope'
        )

    try:
        scopes = scope_for_actor(owner)
    except ScopeError as exc:
        raise ProposalNotFound('no such machine') from exc
    if required not in scopes:
        raise ProposalNotFound('no such machine')


def _dependency_successor(owner, dependency_id):
    """Resolve a dependency to its (scope-authorized) successor card."""
    from tasks.models import KanbanCardDependency

    if not dependency_id:
        raise ProposalError('dependency id is required')
    dependency = KanbanCardDependency.objects.filter(pk=dependency_id).first()
    if dependency is None:
        raise ProposalNotFound('no such dependency')
    return _authorized_work_order(owner, dependency.to_card_id)


def _iso(value: Any) -> Any:
    """Render a datetime as ISO-8601, passing None through unchanged."""
    return value.isoformat() if value is not None else None


def _json_safe(value: Any) -> Any:
    """Coerce dates/datetimes so a preview survives JSONField serialization."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _dt(value: Any) -> datetime | None:
    """Parse an intent datetime, raising a clean 400-mappable error on garbage."""
    if value in (None, ''):
        return None
    parsed = parse_datetime(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime):
        raise ProposalError('intent contains an unparsable datetime')
    return parsed


def _preview_hold(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.models import WorkOrderLifecycle

    return {'resulting_status': str(WorkOrderLifecycle.ON_HOLD)}


def _preview_resume(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.models import WorkOrderLifecycle

    return {'resulting_status': str(WorkOrderLifecycle.IN_PROGRESS)}


def _preview_schedule(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    """Show the true resulting window, deriving the end the command would derive."""
    start = _dt(intent.get('scheduled_start'))
    end = _dt(intent.get('scheduled_end'))
    if end is None and start is not None and work_order.estimated_minutes:
        from tasks.services.calendars import spec_for_card
        from tasks.services.working_time import NoWorkingTime, add_working_minutes

        try:
            end = add_working_minutes(
                spec_for_card(work_order), start, work_order.estimated_minutes
            )
        except NoWorkingTime:
            end = None
    return {
        'current_start': _iso(work_order.scheduled_start),
        'current_end': _iso(work_order.scheduled_end),
        'proposed_start': _iso(start),
        'proposed_end': _iso(end),
        'duration_derived': _dt(intent.get('scheduled_end')) is None,
    }


def _preview_resize(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {
        'current_estimated_minutes': work_order.estimated_minutes,
        'proposed_estimated_minutes': intent.get('estimated_minutes'),
        'current_end': _iso(work_order.scheduled_end),
        'proposed_end': _iso(_dt(intent.get('scheduled_end'))),
    }


def _preview_update(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.services.scheduling import _PLAN_FIELDS

    fields = {
        key: value
        for key, value in (intent.get('fields') or {}).items()
        if key in _PLAN_FIELDS
    }
    return {
        'changes': {
            key: {'from': _json_safe(getattr(work_order, key, None)), 'to': value}
            for key, value in fields.items()
        }
    }


def _preview_assign(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {
        'current_assigned_to_id': work_order.assigned_to_id,
        'proposed_assigned_to_id': intent.get('assigned_to'),
    }


def _preview_delete(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {
        'resulting_status': 'deleted',
        'irreversible': True,
        'confirm_phrase': IRREVERSIBLE_CONFIRM_PHRASE[
            ProposalAction.WORK_ORDER_DELETE.value
        ],
    }


def _preview_cancel(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.models import WorkOrderLifecycle

    return {'resulting_status': str(WorkOrderLifecycle.CANCELED)}


def _preview_transition(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {'resulting_status': intent.get('to_status')}


def _preview_create(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {
        'proposed_title': intent.get('title', ''),
        'machine_id': intent.get('machine_id'),
        'work_order_type': intent.get('work_order_type'),
        'priority': intent.get('priority'),
    }


def _preview_repair_work_package(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    """Build the repair work-package preview from server state, not model claims.

    The model supplies a draft; everything shown to the approver is re-derived
    here. Part names, the machine name, the safety line and the duplicate warning
    all come from the database, so a model cannot describe the package as
    something other than what confirming it would create.
    """
    from assets.models import AssetMachine
    from part.models import Part
    from repair.work_packages import find_duplicate_repairs, validate_draft

    draft = validate_draft(intent)
    machine = AssetMachine.objects.filter(pk=draft['machine_id']).first()

    part_names = dict(
        Part.objects.filter(
            pk__in=[line['part_id'] for line in draft['parts']]
        ).values_list('pk', 'name')
    )

    duplicates = (
        find_duplicate_repairs(machine, anomaly_id=draft['source'].get('anomaly_id'))
        if machine
        else []
    )

    return {
        'machine_id': draft['machine_id'],
        'machine_name': machine.name if machine else None,
        'proposed_title': draft['title'],
        'work_order_type': draft['work_order_type'],
        'priority': draft['priority'],
        'creates_repair_packet': draft['create_repair_packet'],
        'fault': draft['fault'],
        'parts': [
            {
                'part_id': line['part_id'],
                'name': part_names.get(line['part_id'], 'Unknown part'),
                'quantity': str(line['quantity']),
                'reason': line['reason'],
            }
            for line in draft['parts']
        ],
        'planning': draft['planning'],
        'origin': draft['origin'],
        'source': draft['source'],
        # Shown before approval so an approver is never surprised by a second
        # work order for a fault somebody is already working.
        'duplicate_open_repairs': duplicates,
        'creates_planned_work_only': True,
        'note': (
            'Creates a planned work order and repair packet. It does not start '
            'the repair and satisfies no safety gate.'
        ),
    }


def _preview_create_child(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.models import KanbanCard

    return {
        'parent_id': work_order.pk,
        'proposed_title': intent.get('title', ''),
        'card_kind': intent.get('card_kind', KanbanCard.KIND_SUBTASK),
    }


def _preview_generate_procurement(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {
        'parent_id': work_order.pk,
        'note': 'Generates a procurement child for unfulfilled parts, if any.',
    }


def _preview_dependency_create(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    from tasks.models import KanbanCardDependency

    return {
        'from_card_id': intent.get('from_card_id'),
        'to_card_id': work_order.pk,
        'dependency_type': intent.get('dependency_type', KanbanCardDependency.TYPE_FS),
        'lag_minutes': int(intent.get('lag_minutes', 0)),
    }


def _preview_dependency_delete(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    return {'dependency_id': intent.get('dependency_id'), 'successor_id': work_order.pk}


def _preview_optimize(work_order, intent: dict[str, Any]) -> dict[str, Any]:
    candidates = _optimize_candidate_ids(intent)
    return {
        'candidate_ids': candidates,
        'candidate_count': len(candidates),
        'horizon_start': _iso(_dt(intent.get('horizon_start'))),
        'note': 'The planner selects valid slots; the model does not set times.',
    }


#: Each executable action derives its human-facing preview from a fresh read.
_PREVIEW_BUILDERS = {
    ProposalAction.WORK_ORDER_HOLD.value: _preview_hold,
    ProposalAction.WORK_ORDER_RESUME.value: _preview_resume,
    ProposalAction.WORK_ORDER_SCHEDULE.value: _preview_schedule,
    ProposalAction.WORK_ORDER_RESIZE.value: _preview_resize,
    ProposalAction.WORK_ORDER_UPDATE.value: _preview_update,
    ProposalAction.WORK_ORDER_ASSIGN.value: _preview_assign,
    ProposalAction.WORK_ORDER_DELETE.value: _preview_delete,
    ProposalAction.WORK_ORDER_CANCEL.value: _preview_cancel,
    ProposalAction.WORK_ORDER_TRANSITION.value: _preview_transition,
    ProposalAction.WORK_ORDER_CREATE.value: _preview_create,
    ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value: _preview_repair_work_package,
    ProposalAction.WORK_ORDER_CREATE_CHILD.value: _preview_create_child,
    ProposalAction.WORK_ORDER_GENERATE_PROCUREMENT.value: _preview_generate_procurement,
    ProposalAction.DEPENDENCY_CREATE.value: _preview_dependency_create,
    ProposalAction.DEPENDENCY_DELETE.value: _preview_dependency_delete,
    ProposalAction.SCHEDULE_OPTIMIZE.value: _preview_optimize,
}


def _preview(work_order, action_type: str, intent: dict[str, Any]) -> dict[str, Any]:
    """Build the confirmation preview from authoritative state, not model claims.

    ``work_order`` is ``None`` for creating actions (create / optimize), which
    have no pinned card; their builders read only from the (server-validated)
    intent.
    """
    base = {
        'action': action_type,
        'warning': _SAFETY_LINE,
        'as_of': timezone.now().isoformat(),
    }
    if work_order is not None:
        base.update({
            'work_order_id': work_order.pk,
            'reference': getattr(work_order, 'reference', '') or '',
            'title': getattr(work_order, 'title', '') or '',
            'current_status': work_order.lifecycle_status,
        })
    builder = _PREVIEW_BUILDERS.get(action_type)
    if builder is not None:
        base.update(builder(work_order, intent or {}))
    return base


#: The canonical ``tasks.services`` command each executable action dispatches at
#: confirmation. This is the single source of truth for the §5.13 parity
#: invariant: every UI-reachable mutation must appear here, and every entry is
#: dispatched by ``_dispatch`` below (asserted in the parity test). Adding an
#: action means adding both its command mapping here and its ``_dispatch`` branch.
ACTION_COMMAND: dict[str, str] = {
    ProposalAction.WORK_ORDER_HOLD.value: 'hold_work_order',
    ProposalAction.WORK_ORDER_RESUME.value: 'resume_work_order',
    ProposalAction.WORK_ORDER_SCHEDULE.value: 'schedule_work_order',
    ProposalAction.WORK_ORDER_RESIZE.value: 'resize_work_order',
    ProposalAction.WORK_ORDER_UPDATE.value: 'update_work_order_plan',
    ProposalAction.WORK_ORDER_ASSIGN.value: 'assign_work_order',
    ProposalAction.WORK_ORDER_DELETE.value: 'delete_work_order',
    ProposalAction.WORK_ORDER_CANCEL.value: 'cancel_work_order',
    ProposalAction.WORK_ORDER_TRANSITION.value: 'transition_work_order',
    ProposalAction.WORK_ORDER_CREATE.value: 'create_work_order',
    ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value: 'create_repair_work_package',
    ProposalAction.WORK_ORDER_CREATE_CHILD.value: 'create_child',
    ProposalAction.WORK_ORDER_GENERATE_PROCUREMENT.value: 'generate_procurement_child',
    ProposalAction.DEPENDENCY_CREATE.value: 'create_dependency',
    ProposalAction.DEPENDENCY_DELETE.value: 'delete_dependency',
    ProposalAction.SCHEDULE_OPTIMIZE.value: 'apply_schedule_batch',
}

#: The verified executable allow-list. Derived from ACTION_COMMAND so the
#: allow-list and the dispatch mapping can never drift apart.
_ALLOWED_ACTIONS = set(ACTION_COMMAND)


def allowed_actions() -> tuple[str, ...]:
    """Return the executable allow-list snapshot."""
    return tuple(sorted(_ALLOWED_ACTIONS))


def create_proposal(
    *,
    owner,
    scope_key: str,
    scope_hash: str,
    action_type: str,
    work_order_id: int,
    reason: str,
    idempotency_key: str,
    policy_version: str,
    intent: dict[str, Any] | None = None,
    thread_id: str = '',
    source_turn_id: str = '',
    expiry_seconds: int = PROPOSAL_EXPIRY_SECONDS,
) -> ChatActionProposal:
    """Create (or exactly replay) one owner-bound proposal.

    The preview is derived from a fresh server read; the caller-supplied
    reason and intent are stored as quoted untrusted parameters only — the
    canonical command re-reads and re-validates them at confirmation.
    """
    if action_type not in _ALLOWED_ACTIONS:
        raise CapabilityDenied(f'{action_type} is not an executable action')
    if not scope_hash:
        raise ProposalError('scope is unresolved')
    # Permission parity: the actor must hold the same RBAC role the UI requires,
    # checked here (before the read-back) and again at confirmation.
    _require_role(owner, action_type)
    intent = dict(intent or {})
    target_id, target_version, work_order = _authorize_and_bind(
        owner, action_type, work_order_id, intent
    )
    normalized_reason = (reason or '').strip()[:2000]
    # Build the preview first: it validates intent shape (e.g. datetimes) and
    # raises a mappable ProposalError before any row is written.
    preview = _preview(work_order, action_type, intent)
    try:
        with transaction.atomic():
            proposal = ChatActionProposal.objects.create(
                owner=owner,
                scope_key=scope_key,
                scope_hash=scope_hash,
                thread_id=thread_id,
                source_turn_id=source_turn_id,
                action_type=action_type,
                target_work_order_id=target_id,
                target_version=target_version,
                intent=intent,
                preview=preview,
                reason=normalized_reason,
                policy_version=policy_version,
                idempotency_key=idempotency_key,
                expires_at=timezone.now() + timedelta(seconds=expiry_seconds),
            )
            if _approval_queue_owns_execution(action_type):
                _bridge_to_approval(proposal, owner)
            return proposal
    except IntegrityError:
        existing = ChatActionProposal.objects.get(
            owner=owner, idempotency_key=idempotency_key
        )
        if (
            existing.action_type != action_type
            or existing.target_work_order_id != target_id
            or existing.intent != intent
            or existing.scope_key != scope_key
            or existing.scope_hash != scope_hash
            or existing.thread_id != thread_id
            or existing.source_turn_id != source_turn_id
            or existing.reason != normalized_reason
            or existing.policy_version != policy_version
        ):
            raise ProposalStateConflict(
                'a different intent already uses this key'
            ) from None
        return existing


#: Actions bridged to the global approval queue when it owns execution. Kept
#: narrow on purpose: a bridged action must have a registered executor, and the
#: queue must be able to describe it well enough to decide without the chat.
_BRIDGED_ACTIONS = frozenset({ProposalAction.REPAIR_WORK_PACKAGE_CREATE.value})


def _approval_queue_owns_execution(action_type: str) -> bool:
    """Whether the global approval queue is the executor for this action.

    Off by default. A deployment turns it on once its approval inbox is staffed;
    until then the chat rail executes as before, and nothing is bridged.
    """
    from django.conf import settings

    if action_type not in _BRIDGED_ACTIONS:
        return False
    return bool(getattr(settings, 'AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS', False))


def _bridge_to_approval(proposal: ChatActionProposal, owner) -> None:
    """Create the approval row that will execute this proposal, and link it.

    After this the chat proposal is a preview only: :func:`confirm_proposal`
    refuses to dispatch it, so there is exactly one place the effect can happen.
    The executor is required to exist - a missing registration fails here rather
    than at approve time, when a reviewer has already committed to the decision.
    """
    from approvals.executors import registry
    from approvals.models import ActionType, Approval

    action = ActionType.REPAIR_WORK_PACKAGE
    if not registry.has(action):
        raise ProposalError(f'no executor is registered for {action}')

    executor = registry.get(action)
    payload = dict(proposal.intent or {})
    # The command re-checks permission and scope for this actor; naming them here
    # records who asked, it does not widen what they may do.
    payload['actor_id'] = owner.pk

    warnings = executor.validate(payload)
    if warnings:
        raise ProposalError('; '.join(warnings))

    machine_name = proposal.preview.get('machine_name') or 'the machine'
    approval = Approval.objects.create(
        action_type=action,
        summary=f'Create repair work package for {machine_name}: {proposal.preview.get("proposed_title", "")}'[
            :500
        ],
        payload=payload,
        risk_tier=executor.compute_risk_tier(payload),
        baseline_context=executor.compute_baseline(payload),
        source_chat_id=proposal.thread_id,
        agent_run_id=str(proposal.id),
        agent_checkpoint_id=proposal.source_turn_id or str(proposal.id),
        tool_call_id=f'chat-proposal:{proposal.id}',
        idempotency_key=f'chat-proposal:{proposal.id}',
        assigned_to_user=owner,
    )

    proposal.approval = approval
    proposal.save(update_fields=['approval', 'updated_at'])


def get_owned_proposal(*, owner, scope_hash: str, proposal_id) -> ChatActionProposal:
    """Owner-safe lookup; existence is never disclosed across owners."""
    try:
        return ChatActionProposal.objects.get(
            id=proposal_id, owner=owner, scope_hash=scope_hash
        )
    except Exception as exc:  # DoesNotExist / ValidationError / ValueError
        raise ProposalNotFound('no such proposal') from exc


def list_owned_proposals(*, owner, scope_hash: str, limit: int = 20):
    """Return the owner's most recent proposals."""
    return list(
        ChatActionProposal.objects.filter(owner=owner, scope_hash=scope_hash)[
            : max(1, limit)
        ]
    )


def reject_proposal(*, owner, scope_hash: str, proposal_id) -> ChatActionProposal:
    """Idempotently reject a pending proposal."""
    with transaction.atomic():
        proposal = (
            ChatActionProposal.objects
            .select_for_update()
            .filter(id=proposal_id, owner=owner, scope_hash=scope_hash)
            .first()
        )
        if proposal is None:
            raise ProposalNotFound('no such proposal')
        if proposal.state == ProposalState.REJECTED:
            return proposal
        if proposal.is_terminal:
            raise ProposalStateConflict(f'proposal is {proposal.state}')
        proposal.state = ProposalState.REJECTED
        proposal.save(update_fields=['state', 'updated_at'])
        return proposal


def _command_receipt(result) -> dict[str, Any]:
    """Serialize a lifecycle ``CommandResult`` into a durable receipt."""
    return {
        'work_order_id': result.work_order_id,
        'event_id': result.event_id,
        'command': result.command,
        'lifecycle_status': result.lifecycle_status,
        'lifecycle_version': result.lifecycle_version,
        'correlation_id': str(result.correlation_id),
        'idempotency_key': result.idempotency_key,
    }


def _deletion_receipt(result) -> dict[str, Any]:
    """Serialize a governed-delete ``DeletionResult`` into a durable receipt."""
    return {
        'work_order_id': result.work_order_id,
        'deletion_record_id': result.deletion_record_id,
        'command': 'delete',
        'reference': result.reference,
        'correlation_id': str(result.correlation_id),
        'idempotency_key': result.idempotency_key,
    }


def _child_receipt(child) -> dict[str, Any]:
    """Receipt for ``generate_procurement_child`` (returns a card or None)."""
    if child is None:
        return {
            'command': 'generate_procurement',
            'child_id': None,
            'note': 'no procurement child was needed',
        }
    return {
        'command': 'generate_procurement',
        'child_id': child.pk,
        'parent_id': child.parent_id,
        'reference': child.reference or '',
    }


def _dependency_create_receipt(dependency) -> dict[str, Any]:
    """Receipt for ``create_dependency`` (returns the edge)."""
    return {
        'command': 'create_dependency',
        'dependency_id': dependency.pk,
        'from_card_id': dependency.from_card_id,
        'to_card_id': dependency.to_card_id,
        'dependency_type': dependency.dependency_type,
    }


def _create_planning(intent: dict[str, Any]) -> dict[str, Any]:
    """The planning fields create/create_child accept (mirrors the REST view)."""
    return {
        key: intent[key]
        for key in (
            'description',
            'priority',
            'work_order_type',
            'assignee',
            'due_date',
        )
        if key in intent
    }


def _dispatch_optimize(owner, intent: dict[str, Any], idem: str) -> dict[str, Any]:
    """Bulk optimize: re-plan deterministically, then apply atomically.

    The planner (not the model) chooses slots; ``apply_schedule_batch`` uses each
    card's *current* version so a concurrent change fails the whole apply.
    """
    from tasks.models import KanbanCard
    from tasks.services import scheduling
    from tasks.services.schedule_planner import PlanRequest, plan_schedule

    request = PlanRequest(
        candidate_ids=_optimize_candidate_ids(intent),
        horizon_start=_dt(intent.get('horizon_start')) or timezone.now(),
        locked_ids=frozenset(int(x) for x in (intent.get('locked_ids') or [])),
        allow_move_existing=bool(intent.get('allow_move_existing', True)),
        check_assignee=bool(intent.get('check_assignee')),
    )
    plan = plan_schedule(request)
    if not plan.operations:
        return {
            'command': 'optimize',
            'applied': [],
            'unscheduled': list(plan.unscheduled),
            'warnings': list(plan.warnings),
        }
    versions = dict(
        KanbanCard.objects.filter(
            id__in=[op.card_id for op in plan.operations]
        ).values_list('id', 'lifecycle_version')
    )
    operations = [
        {
            'card_id': op.card_id,
            'expected_version': versions[op.card_id],
            'scheduled_start': op.new_start,
            'scheduled_end': op.new_end,
        }
        for op in plan.operations
    ]
    results = scheduling.apply_schedule_batch(
        actor=owner, idempotency_key=idem, operations=operations
    )
    return {
        'command': 'optimize',
        'applied': [
            {'work_order_id': r.work_order_id, 'lifecycle_version': r.lifecycle_version}
            for r in results
        ],
        'unscheduled': list(plan.unscheduled),
        'warnings': list(plan.warnings),
    }


def _dispatch(proposal: ChatActionProposal, owner) -> dict[str, Any]:
    """Dispatch the one canonical command an allow-listed action maps to.

    Every branch calls the same ``tasks.services`` command the UI uses — same
    permission check, same optimistic-version guard, same audit event — with
    the stored intent. The command, not this function, is the security
    boundary; this only routes and shapes the receipt.
    """
    from tasks.services import scheduling
    from tasks.services import work_orders as wo_commands

    action = proposal.action_type
    idem = f'proposal:{proposal.id}'
    wo_id = proposal.target_work_order_id
    version = proposal.target_version
    intent = proposal.intent or {}
    reason = proposal.reason or f'Confirmed chat proposal {proposal.id}'

    if action == ProposalAction.WORK_ORDER_HOLD:
        return _command_receipt(
            wo_commands.hold_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_RESUME:
        return _command_receipt(
            wo_commands.resume_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_SCHEDULE:
        return _command_receipt(
            scheduling.schedule_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                scheduled_start=_dt(intent.get('scheduled_start')),
                scheduled_end=_dt(intent.get('scheduled_end')),
            )
        )
    if action == ProposalAction.WORK_ORDER_RESIZE:
        return _command_receipt(
            scheduling.resize_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                estimated_minutes=intent.get('estimated_minutes'),
                scheduled_end=_dt(intent.get('scheduled_end')),
            )
        )
    if action == ProposalAction.WORK_ORDER_UPDATE:
        return _command_receipt(
            scheduling.update_work_order_plan(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                fields=dict(intent.get('fields') or {}),
            )
        )
    if action == ProposalAction.WORK_ORDER_ASSIGN:
        return _command_receipt(
            wo_commands.assign_work_order(
                work_order_id=wo_id,
                assigned_to=intent.get('assigned_to'),
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_DELETE:
        return _deletion_receipt(
            scheduling.delete_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_CANCEL:
        return _command_receipt(
            wo_commands.cancel_work_order(
                work_order_id=wo_id,
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_TRANSITION:
        return _command_receipt(
            wo_commands.transition_work_order(
                work_order_id=wo_id,
                to_status=intent.get('to_status'),
                actor=owner,
                expected_version=version,
                idempotency_key=idem,
                reason=reason,
            )
        )
    if action == ProposalAction.WORK_ORDER_CREATE:
        return _command_receipt(
            scheduling.create_work_order(
                actor=owner,
                idempotency_key=idem,
                title=intent.get('title', ''),
                machine_id=intent.get('machine_id'),
                **_create_planning(intent),
            )
        )
    if action == ProposalAction.REPAIR_WORK_PACKAGE_CREATE:
        from repair.work_packages import create_repair_work_package

        result = create_repair_work_package(
            actor=owner, draft=intent, idempotency_key=idem
        )
        return {'command': 'create_repair_work_package', **result.as_dict()}
    if action == ProposalAction.WORK_ORDER_CREATE_CHILD:
        from tasks.models import KanbanCard

        return _command_receipt(
            scheduling.create_child(
                parent_id=wo_id,
                actor=owner,
                idempotency_key=idem,
                title=intent.get('title', ''),
                card_kind=intent.get('card_kind', KanbanCard.KIND_SUBTASK),
                **_create_planning(intent),
            )
        )
    if action == ProposalAction.WORK_ORDER_GENERATE_PROCUREMENT:
        return _child_receipt(
            scheduling.generate_procurement_child(parent_id=wo_id, actor=owner)
        )
    if action == ProposalAction.DEPENDENCY_CREATE:
        from tasks.models import KanbanCardDependency

        return _dependency_create_receipt(
            scheduling.create_dependency(
                from_card_id=int(intent['from_card_id']),
                to_card_id=wo_id,
                actor=owner,
                dependency_type=intent.get(
                    'dependency_type', KanbanCardDependency.TYPE_FS
                ),
                lag_minutes=int(intent.get('lag_minutes', 0)),
            )
        )
    if action == ProposalAction.DEPENDENCY_DELETE:
        removed = scheduling.delete_dependency(
            dependency_id=int(intent['dependency_id']), actor=owner
        )
        return {
            'command': 'delete_dependency',
            'dependency_id': int(intent['dependency_id']),
            'removed': bool(removed),
        }
    if action == ProposalAction.SCHEDULE_OPTIMIZE:
        return _dispatch_optimize(owner, intent, idem)
    # Unreachable: create_proposal gates action_type against the same allow-list.
    raise CapabilityDenied(f'{action} has no dispatcher')


def confirm_proposal(
    *,
    owner,
    scope_hash: str,
    proposal_id,
    confirm_phrase: str = '',
    strict_phrase_satisfied: bool = False,
) -> ChatActionProposal:
    """Execute one confirmed proposal through the canonical command service.

    Reauthorization happens inside the command itself (permission, scope,
    expected version, legal transition, readiness); this function adds the
    proposal-level guards: ownership, expiry, terminal-state, the strict-phrase
    check for irreversible actions, and exactly-once dispatch under a row lock.
    Replaying a confirmed proposal returns the stored receipt without a second
    effect.

    ``strict_phrase_satisfied`` lets the voice rail assert it already enforced
    the strict phrase verbally at the gate, so the phrase is not demanded twice;
    the text rail always passes the actor-supplied ``confirm_phrase``.
    """
    from tasks.services import work_orders as wo_commands

    try:
        with transaction.atomic():
            proposal = (
                ChatActionProposal.objects
                .select_for_update()
                .filter(id=proposal_id, owner=owner, scope_hash=scope_hash)
                .first()
            )
            if proposal is None:
                raise ProposalNotFound('no such proposal')
            if proposal.state == ProposalState.EXECUTED:
                return proposal  # exact replay of the recorded outcome
            if proposal.is_terminal:
                raise ProposalStateConflict(f'proposal is {proposal.state}')
            if timezone.now() > proposal.expires_at:
                raise ProposalExpired('proposal expired before confirmation')
            # Irreversible actions demand their exact strict phrase — the same
            # control voice enforces verbally, now on the text rail (§5.3). Voice
            # asserts it already did this at the gate; text always supplies it.
            if not strict_phrase_satisfied:
                _require_strict_phrase(proposal.action_type, confirm_phrase)
            # Re-check the RBAC role at execution time: a grant may have been
            # revoked between propose and confirm (§5.3 defense in depth).
            _require_role(owner, proposal.action_type)

            # Exactly one execution authority. When this proposal was bridged to
            # the global approval queue, that queue is the executor and this rail
            # must not dispatch: doing both would run the effect twice.
            if proposal.approval_id is not None:
                raise ApprovalOwnsExecution(
                    'This action is executed from the approval queue; '
                    'open the linked approval to decide it.'
                )

            proposal.state = ProposalState.EXECUTED
            proposal.confirmed_at = timezone.now()
            proposal.receipt = _dispatch(proposal, owner)
            proposal.save(
                update_fields=['state', 'confirmed_at', 'receipt', 'updated_at']
            )
            return proposal
    except ProposalExpired:
        # Persist the terminal marker outside the rolled-back transaction.
        _mark(owner, scope_hash, proposal_id, ProposalState.EXPIRED, '')
        raise
    except wo_commands.StaleVersion as exc:
        _mark(
            owner,
            scope_hash,
            proposal_id,
            ProposalState.FAILED,
            'PROPOSAL_REVALIDATION_FAILED',
        )
        raise ProposalRevalidationFailed(str(exc)) from exc
    except wo_commands.WorkOrderCommandError as exc:
        _mark(
            owner,
            scope_hash,
            proposal_id,
            ProposalState.FAILED,
            exc.__class__.__name__[:64],
        )
        raise ProposalStateConflict(str(exc)) from exc


def _mark(owner, scope_hash: str, proposal_id, state: str, code: str) -> None:
    """Record a terminal outcome in its own committed transaction."""
    with transaction.atomic():
        ChatActionProposal.objects.filter(
            id=proposal_id,
            owner=owner,
            scope_hash=scope_hash,
            state=ProposalState.PROPOSED,
        ).update(state=state, failure_code=code, updated_at=timezone.now())


def expire_stale_proposals() -> int:
    """Scheduled sweep (adopted default: 15-minute cadence)."""
    return ChatActionProposal.objects.filter(
        state=ProposalState.PROPOSED, expires_at__lt=timezone.now()
    ).update(state=ProposalState.EXPIRED, updated_at=timezone.now())
