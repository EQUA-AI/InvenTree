"""Work-order decisions use the same durable proposal as touch confirmation."""

from ai.core.decisions.adapters.registry import action_spec
from ai.core.decisions.resolver import WorkOrderIntent
from ai.core.decisions.work_order_review import FIELDS


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

        if intent.action.startswith("stock."):
            from ai.core.config import get_settings
            from ai.core.decisions.inventory import resolve_parameters

            if not get_settings().feature_voice_inventory_actions:
                raise proposals.ProposalError("Inventory actions by voice are disabled.")
            owner, (scope_key, scope_hash) = self.owner_scope(actor)
            params = resolve_parameters(owner, intent.parameters)
            return proposals.create_proposal(
                owner=owner,
                scope_key=scope_key,
                scope_hash=scope_hash,
                action_type=intent.action,
                work_order_id=None,
                reason=intent.reason,
                intent={**params, "reason": intent.reason},
                idempotency_key=f"voice-decision:{nonce}",
                policy_version="voice-inventory-v1",
                thread_id=str(thread_id),
                source_turn_id=str(nonce),
                expiry_seconds=360,
            )
        if (
            intent.action not in ("work_order.hold", "work_order.cancel", *FIELDS)
            or not action_spec(intent.action).available
        ):
            raise proposals.ProposalError("That work-order action is not available by voice yet.")
        if (
            intent.action in ("work_order.hold", "work_order.cancel", "work_order.delete")
            and not intent.reason.strip()
        ):
            action = intent.action.removeprefix("work_order.")
            if action == "cancel":
                action = "cancellation"
            raise proposals.ProposalError(f"Say the work order and the reason for the {action}.")
        owner, (scope_key, scope_hash) = self.owner_scope(actor)
        self.require_action_permission(owner, intent.action, intent.parameters)
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
        params = dict(intent.parameters)
        allowed = {
            "closeout.consent": {"live_session_id"},
            "closeout.accept": {
                "live_session_id",
                "capture_id",
                "revision_id",
                "content_hash",
                "auditory_review_id",
            },
            "closeout.handoff": {
                "live_session_id",
                "capture_id",
                "revision_id",
                "content_hash",
                "auditory_review_id",
            },
            "procedure.complete": {"application_id", "step_key", "value", "passed"},
            "work_order.assign": {"assignee_name"},
            "work_order.resize": {"estimated_minutes"},
            "work_order.schedule": {"scheduled_start", "scheduled_end"},
            "work_order.update": {"fields"},
            "work_order.transition": {"to_status"},
            "work_order.create_child": {"title"},
            "dependency.create": {"predecessor_reference", "dependency_type", "lag_minutes"},
            "dependency.delete": {"dependency_id"},
        }.get(intent.action, set())
        if set(params) - allowed:
            raise proposals.ProposalError("Unsupported voice parameters; request a new preview.")
        if intent.action == "work_order.update" and (
            not isinstance(params.get("fields"), dict)
            or set(params["fields"]) - {"title", "description", "priority"}
        ):
            raise proposals.ProposalError(
                "Only title, description and priority can be updated by voice."
            )
        if intent.action == "work_order.assign":
            from django.contrib.auth import get_user_model
            from django.db.models import Q, Value
            from django.db.models.functions import Concat

            name = str(params.pop("assignee_name", "")).strip()
            if name.lower() == "unassigned":
                params["assigned_to"] = None
            else:
                users = list(
                    get_user_model()
                    .objects.filter(is_active=True)
                    .annotate(display_name=Concat("first_name", Value(" "), "last_name"))
                    .filter(Q(username__iexact=name) | Q(display_name__iexact=name))[:2]
                )
                if len(users) != 1:
                    raise proposals.ProposalError(
                        "Say one exact assignee username; the name is missing or ambiguous."
                    )
                params["assigned_to"] = users[0].pk
        if intent.action == "dependency.create":
            ref = str(params.pop("predecessor_reference", ""))
            predecessors = list(WorkOrder.objects.filter(reference__iexact=ref)[:2])
            if not predecessors and ref.isdecimal():
                predecessors = list(WorkOrder.objects.filter(pk=int(ref))[:1])
            if len(predecessors) != 1:
                raise proposals.ProposalError("Say one exact predecessor work-order reference.")
            params["predecessor_id"] = predecessors[0].pk
        if intent.action == "dependency.delete":
            from tasks.models import WorkOrderDependency

            if not WorkOrderDependency.objects.filter(
                pk=params.get("dependency_id"), successor=rows[0]
            ).exists():
                raise proposals.ProposalError(
                    "That dependency does not belong to the reviewed work order."
                )
        if intent.action == "work_order.schedule":
            from django.utils.timezone import is_aware

            for key in ("scheduled_start", "scheduled_end"):
                date = proposals._dt(params.get(key))
                if date is None or not is_aware(date):
                    raise proposals.ProposalError(
                        "Use an exact date and time with a timezone for both schedule bounds."
                    )
        return proposals.create_proposal(
            owner=owner,
            scope_key=scope_key,
            scope_hash=scope_hash,
            action_type=intent.action,
            work_order_id=rows[0].pk,
            reason=intent.reason,
            intent={**params, "reason": intent.reason},
            idempotency_key=f"voice-decision:{nonce}",
            policy_version="voice-decision-v1",
            thread_id=str(thread_id),
            source_turn_id=str(nonce),
            expiry_seconds=360,
        )

    @staticmethod
    def require_action_permission(owner, action, params):
        """Match the canonical lifecycle permission or the scheduling role."""
        from tasks.permissions import (
            ASSIGN_WORKORDER,
            EXECUTE_WORKORDER,
            TRANSITION_WORKORDER,
            require_permission,
            transition_permission,
        )

        permission = {
            "work_order.hold": EXECUTE_WORKORDER,
            "work_order.resume": EXECUTE_WORKORDER,
            "work_order.cancel": TRANSITION_WORKORDER,
            "work_order.assign": ASSIGN_WORKORDER,
        }.get(action)
        if action == "work_order.transition":
            permission = transition_permission(params.get("to_status"))
        if permission:
            require_permission(owner, permission)
        from aichat.services.proposals import _require_role

        _require_role(owner, action)

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
            if (
                proposal.action_type == "procedure.complete"
                and not action_spec("procedure.complete").available
            ):
                raise proposals.ProposalError("Procedure completion by voice is disabled.")
            if proposal.action_type.startswith("stock."):
                from ai.core.config import get_settings

                if not get_settings().feature_voice_inventory_actions:
                    raise proposals.ProposalError("Inventory actions by voice are disabled.")
            self.require_action_permission(owner, proposal.action_type, proposal.intent)
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

        # Workflow flags may have changed after the coordinator's initial read.
        self.read(decision, actor)
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
