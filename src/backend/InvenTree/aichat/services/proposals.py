"""Governed action proposals (WS7).

The only executable actions are the verified allow-list builders below.
Creation re-reads the work order server-side and snapshots its version;
confirmation reauthorizes under a row lock and dispatches the canonical
work-order command service — never an AI tool — recording the real receipt
exactly once. Speech, transcripts, and model output are quoted, untrusted
display data throughout.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

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


def _preview(work_order, action_type: str) -> dict[str, Any]:
    from tasks.models import WorkOrderLifecycle

    resulting = (
        WorkOrderLifecycle.ON_HOLD
        if action_type == ProposalAction.WORK_ORDER_HOLD
        else WorkOrderLifecycle.IN_PROGRESS
    )
    return {
        'action': action_type,
        'work_order_id': work_order.pk,
        'reference': getattr(work_order, 'reference', '') or '',
        'title': getattr(work_order, 'title', '') or '',
        'current_status': work_order.lifecycle_status,
        'resulting_status': str(resulting),
        'warning': _SAFETY_LINE,
        'as_of': timezone.now().isoformat(),
    }


#: The verified executable allow-list. Adding an action requires its own
#: canonical command mapping and security review (contract §3.2).
_ALLOWED_ACTIONS = {
    ProposalAction.WORK_ORDER_HOLD.value,
    ProposalAction.WORK_ORDER_RESUME.value,
}


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
    thread_id: str = '',
    source_turn_id: str = '',
) -> ChatActionProposal:
    """Create (or exactly replay) one owner-bound proposal.

    The preview is derived from a fresh server read; the caller-supplied
    reason is stored as quoted untrusted text only.
    """
    if action_type not in _ALLOWED_ACTIONS:
        raise CapabilityDenied(f'{action_type} is not an executable action')
    if not scope_hash:
        raise ProposalError('scope is unresolved')
    work_order = _authorized_work_order(owner, int(work_order_id))
    normalized_reason = (reason or '').strip()[:2000]
    try:
        with transaction.atomic():
            return ChatActionProposal.objects.create(
                owner=owner,
                scope_key=scope_key,
                scope_hash=scope_hash,
                thread_id=thread_id,
                source_turn_id=source_turn_id,
                action_type=action_type,
                target_work_order_id=work_order.pk,
                target_version=work_order.lifecycle_version,
                preview=_preview(work_order, action_type),
                reason=normalized_reason,
                policy_version=policy_version,
                idempotency_key=idempotency_key,
                expires_at=timezone.now() + timedelta(seconds=PROPOSAL_EXPIRY_SECONDS),
            )
    except IntegrityError:
        existing = ChatActionProposal.objects.get(
            owner=owner, idempotency_key=idempotency_key
        )
        if (
            existing.action_type != action_type
            or existing.target_work_order_id != work_order.pk
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


def confirm_proposal(*, owner, scope_hash: str, proposal_id) -> ChatActionProposal:
    """Execute one confirmed proposal through the canonical command service.

    Reauthorization happens inside the command itself (permission, scope,
    expected version, legal transition, readiness); this function adds the
    proposal-level guards: ownership, expiry, terminal-state, and
    exactly-once dispatch under a row lock. Replaying a confirmed proposal
    returns the stored receipt without a second effect.
    """
    from tasks.services import work_orders as wo_commands

    try:  # noqa: PLW0717
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

            command = (
                wo_commands.hold_work_order
                if proposal.action_type == ProposalAction.WORK_ORDER_HOLD
                else wo_commands.resume_work_order
            )
            result = command(
                work_order_id=proposal.target_work_order_id,
                actor=owner,
                expected_version=proposal.target_version,
                idempotency_key=f'proposal:{proposal.id}',
                reason=proposal.reason or f'Confirmed chat proposal {proposal.id}',
            )

            proposal.state = ProposalState.EXECUTED
            proposal.confirmed_at = timezone.now()
            proposal.receipt = {
                'work_order_id': result.work_order_id,
                'event_id': result.event_id,
                'command': result.command,
                'lifecycle_status': result.lifecycle_status,
                'lifecycle_version': result.lifecycle_version,
                'correlation_id': str(result.correlation_id),
                'idempotency_key': result.idempotency_key,
            }
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
