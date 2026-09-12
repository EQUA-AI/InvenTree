"""Hold decisions use the very same durable proposal as touch confirmation."""

from ai.core.decisions.adapters.registry import action_spec
from ai.core.decisions.resolver import WorkOrderIntent


class ProposalAdapter:
    """Synchronous ORM seam; callers use the thread-sensitive executor."""

    def owner_scope(self, actor):
        """Rehydrate activity, permissions and business scope on every interaction."""
        from aichat.services.scope_strings import scope_strings
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.get(pk=actor.user_pk, is_active=True)
        return owner, scope_strings(owner)

    def create(self, intent: WorkOrderIntent, *, actor, thread_id, nonce):
        """Resolve the exact record and capture an authoritative preview."""
        from aichat.services import proposals
        from tasks.models import WorkOrder

        if not action_spec(intent.action).available or not intent.reason:
            raise proposals.ProposalError("Say the work order and the reason for the hold.")
        owner, (scope_key, scope_hash) = self.owner_scope(actor)
        from tasks.permissions import EXECUTE_WORKORDER, require_permission

        require_permission(owner, EXECUTE_WORKORDER)
        ref = intent.reference.strip()
        # Prefer the actual reference; numeric PK is supported only when there
        # is no reference match. Never choose between ambiguous records.
        rows = list(WorkOrder.objects.filter(reference__iexact=ref)[:2])
        if not rows and ref.isdecimal():
            rows = list(WorkOrder.objects.filter(pk=int(ref))[:1])
        if len(rows) != 1:
            raise proposals.ProposalError(
                "I could not identify one work order. Say its exact reference."
            )
        return proposals.create_proposal(
            owner=owner,
            scope_key=scope_key,
            scope_hash=scope_hash,
            action_type=intent.action,
            work_order_id=rows[0].pk,
            reason=intent.reason,
            intent={"reason": intent.reason},
            idempotency_key=f"voice-decision:{nonce}",
            policy_version="voice-decision-v1",
            thread_id=str(thread_id),
            source_turn_id=str(nonce),
            expiry_seconds=360,
        )

    def read(self, decision, actor):
        """Read the owner-bound proposal under the actor's current scope."""
        from aichat.services import proposals

        owner, (_, scope_hash) = self.owner_scope(actor)
        if scope_hash != decision.scope_hash:
            raise proposals.ProposalError("Your scope changed; request a fresh preview.")
        proposal = proposals.get_owned_proposal(
            owner=owner, scope_hash=scope_hash, proposal_id=decision.source_id
        )
        if proposal.state == "proposed":
            _, version, target = proposals._authorize_and_bind(
                owner, proposal.action_type, proposal.target_work_order_id, proposal.intent
            )
            current_hash = proposals.compute_preview_hash(
                proposals._preview(target, proposal.action_type, proposal.intent)
            )
            if version != decision.revision or current_hash != decision.preview_hash:
                raise proposals.ProposalPreviewChanged(
                    "The work order changed; request a fresh preview."
                )
        return proposal

    def execute(self, decision, actor, phrase):
        """Re-derive scope, then atomically revalidate/execute with exact receipt."""
        from aichat.services import proposals

        owner, (_, scope_hash) = self.owner_scope(actor)
        if scope_hash != decision.scope_hash:
            raise proposals.ProposalError("Your scope changed; request a fresh preview.")
        return proposals.confirm_proposal(
            owner=owner,
            scope_hash=scope_hash,
            proposal_id=decision.source_id,
            expected_preview_hash=decision.preview_hash,
            confirm_phrase=phrase,
        )

    def reject(self, decision, actor):
        """Cancel the durable source only for an explicit source-cancel command."""
        from aichat.services import proposals

        owner, (_, scope_hash) = self.owner_scope(actor)
        return proposals.reject_proposal(
            owner=owner, scope_hash=scope_hash, proposal_id=decision.source_id
        )
