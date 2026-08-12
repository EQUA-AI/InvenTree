"""Voice → governed-proposal bridge (Phase 6e / §5.3 unification).

The voice Tier-3 write gate (``ai.core.voice.write_gate``) is deliberately
seam-based: a deployment injects the *executor* that runs a verbally-confirmed,
re-authorized write. Historically that executor terminated in the direct-ORM
kanban tools, so a voice-confirmed write got no ``expected_version`` check, no
``WorkOrderEvent``, no customer scope and no receipt (§5.3).

This module supplies the executor that unifies voice onto the single write path:
it dispatches a **pre-created** ``ChatActionProposal`` through the shared command
service, exactly as the visual confirm endpoint does. Because the proposal is
created at propose-time (by ``proposals.create_proposal`` — the same server-
derived preview the read-back is spoken from) and merely *confirmed* here, the
gate's human verbal confirmation remains the authenticating act; the AI never
confirms its own proposal. Voice keeps its verbal-confirmation UX and gets the
version guard, audit event, scope check and exactly-once receipt for free.

The executor is intentionally tiny and single-responsibility: it confirms one
owner-bound proposal by id and reports the outcome. It never creates proposals,
never resolves speech and never touches the ORM outside the command service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async

from ai.core.voice.confirmation import ProposedWriteAction, WriteActionClass
from ai.core.voice.write_gate import (
    ExecutableWrite,
    ResolvedVoiceWrite,
    VoiceWriteExecutionResult,
)
from aichat.models import ProposalAction
from aichat.services import proposals

if TYPE_CHECKING:
    from ai.core.auth import AIPrincipal
    from aichat.models import ChatActionProposal

#: Voice exchanges are short; a spoken proposal should not linger as long as a
#: visual one (§5.3 point 4). Confirmation is expected on the next turn.
VOICE_PROPOSAL_EXPIRY_SECONDS = 3 * 60

#: Irreversible actions and their strict phrase — shared with the text rail so
#: both demand the same control (§5.3 point 3).
_IRREVERSIBLE_ACTIONS = frozenset(proposals.IRREVERSIBLE_CONFIRM_PHRASE)

#: Spoken verbs for the read-back. Server-authored, never model text.
_ACTION_VERB = {
    ProposalAction.WORK_ORDER_HOLD.value: 'put on hold',
    ProposalAction.WORK_ORDER_RESUME.value: 'resume',
    ProposalAction.WORK_ORDER_SCHEDULE.value: 'reschedule',
    ProposalAction.WORK_ORDER_RESIZE.value: 'change the duration of',
    ProposalAction.WORK_ORDER_UPDATE.value: 'update',
    ProposalAction.WORK_ORDER_ASSIGN.value: 'reassign',
    ProposalAction.WORK_ORDER_DELETE.value: 'permanently delete',
    ProposalAction.WORK_ORDER_CANCEL.value: 'cancel',
    ProposalAction.WORK_ORDER_TRANSITION.value: 'move the lifecycle of',
    ProposalAction.WORK_ORDER_CREATE_CHILD.value: 'add a child to',
    ProposalAction.WORK_ORDER_GENERATE_PROCUREMENT.value: 'generate procurement for',
    ProposalAction.DEPENDENCY_CREATE.value: 'add a dependency to',
    ProposalAction.DEPENDENCY_DELETE.value: 'remove a dependency from',
}


def _owner(actor: AIPrincipal):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(pk=actor.user_pk)


def _voice_summary(action_type: str, preview: dict[str, Any]) -> str:
    """Server-authored spoken read-back, derived from the proposal preview."""
    if action_type == ProposalAction.WORK_ORDER_CREATE.value:
        return f'Create work order {preview.get("proposed_title", "")!r}. Confirm?'
    if action_type == ProposalAction.SCHEDULE_OPTIMIZE.value:
        return (
            f'Auto-schedule {preview.get("candidate_count", 0)} work orders. Confirm?'
        )
    verb = _ACTION_VERB.get(action_type, 'change')
    subject = preview.get('reference') or f'work order {preview.get("work_order_id")}'
    return f"I'll {verb} {subject}. Confirm?"


def _proposed_action(
    proposal: ChatActionProposal, capability: str
) -> ProposedWriteAction:
    """Build the voice policy view (summary + irreversibility tier) from a proposal."""
    return ProposedWriteAction(
        capability=capability,
        summary=_voice_summary(proposal.action_type, proposal.preview or {}),
        action_class=(
            WriteActionClass.IRREVERSIBLE
            if proposal.action_type in _IRREVERSIBLE_ACTIONS
            else WriteActionClass.CONFIRMABLE
        ),
        confirm_phrase=proposals.IRREVERSIBLE_CONFIRM_PHRASE.get(
            proposal.action_type, ''
        ),
    )


def build_voice_proposal(
    *,
    owner,
    scope_key: str,
    scope_hash: str,
    action_type: str,
    work_order_id=None,
    intent: dict[str, Any] | None = None,
    reason: str = '',
    idempotency_key: str,
    policy_version: str = 'ws7-voice-v1',
    thread_id: str = '',
    source_turn_id: str = '',
    correlation_id: str = '',
    capability: str = 'work_order.change',
) -> ResolvedVoiceWrite:
    """Propose-side of the unified rail: create a proposal, return a resolved write.

    A deployment's speech resolver parses the transcript into a structured intent
    and calls this. It creates a durable ``ChatActionProposal`` (the same store,
    scope and idempotency the text rail uses, on a shorter voice expiry), then
    returns the ``ResolvedVoiceWrite`` the gate needs: the server-authored spoken
    read-back and irreversibility tier, plus an ``ExecutableWrite`` bound to the
    proposal id. The gate later confirms it via ``ProposalConfirmingVoiceExecutor``
    — nothing dispatches a domain command until that verbal confirmation.
    """
    proposal = proposals.create_proposal(
        owner=owner,
        scope_key=scope_key,
        scope_hash=scope_hash,
        action_type=action_type,
        work_order_id=work_order_id,
        reason=reason,
        idempotency_key=idempotency_key,
        policy_version=policy_version,
        intent=intent or {},
        thread_id=thread_id,
        source_turn_id=source_turn_id,
        correlation_id=correlation_id,
        expiry_seconds=VOICE_PROPOSAL_EXPIRY_SECONDS,
    )
    executable = ExecutableWrite(
        tool_name=action_type,
        capability=capability,
        arguments={'proposal_id': str(proposal.id), 'scope_hash': scope_hash},
    )
    return ResolvedVoiceWrite(
        action=_proposed_action(proposal, capability), executable=executable
    )


class ProposalConfirmingVoiceExecutor:
    """Executor seam: confirm a governed proposal, the one voice write path.

    Conforms to ``ai.core.voice.write_gate.VoiceWriteExecutor``. The gate calls
    ``execute`` only inside its confirmed-write fence, after a verbal
    confirmation and a fresh re-authorization. ``executable.arguments`` must
    carry ``proposal_id`` and ``scope_hash`` (bound at propose-time); the
    canonical command re-checks ownership, expiry, expected-version and
    readiness, so a stale or cross-owner proposal fails closed here.
    """

    async def execute(
        self, executable: ExecutableWrite, *, actor: AIPrincipal, trusted_context: Any
    ) -> VoiceWriteExecutionResult:
        """Confirm the bound proposal through the shared command service."""
        return await sync_to_async(self._confirm, thread_sensitive=True)(
            executable, actor
        )

    def _confirm(
        self, executable: ExecutableWrite, actor: AIPrincipal
    ) -> VoiceWriteExecutionResult:
        args = executable.arguments or {}
        proposal_id = args.get('proposal_id')
        scope_hash = args.get('scope_hash')
        if not proposal_id or not scope_hash:
            return VoiceWriteExecutionResult(
                ok=False, detail='PROPOSAL_BINDING_MISSING'
            )
        try:
            owner = _owner(actor)
            confirmed = proposals.confirm_proposal(
                owner=owner,
                scope_hash=scope_hash,
                proposal_id=proposal_id,
                # The voice gate already enforced the strict phrase verbally for
                # an irreversible action before reaching this executor.
                strict_phrase_satisfied=True,
            )
        except proposals.ProposalError as exc:
            # Fail closed: expiry, staleness, terminal-state and cross-owner all
            # surface as a spoken failure, never a silent or partial write.
            return VoiceWriteExecutionResult(ok=False, detail=exc.code)
        except Exception:
            # Defensive: a malformed id or missing user must not 500 the voice
            # turn; it fails closed as an unspoken write, like any other refusal.
            return VoiceWriteExecutionResult(ok=False, detail='PROPOSAL_NOT_FOUND')
        command = (confirmed.receipt or {}).get('command', '')
        return VoiceWriteExecutionResult(ok=True, detail=command)
