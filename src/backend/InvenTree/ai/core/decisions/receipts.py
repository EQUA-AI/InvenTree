"""Authoritative operation lookup; absence of a receipt never proves no effect."""


def start_operation(decision):
    """Persist submission before the effect; return the unique decision ledger."""
    from voice.models import VoiceOperation

    return VoiceOperation.objects.get_or_create(
        decision_id=decision.decision_id,
        defaults={
            "session_id": decision.session_id,
            "source_id": decision.source_id,
            "action": (decision.executable or {}).get("action", ""),
            "target_label": decision.target_label,
        },
    )


def finish_operation(operation, *, state, receipt=None, detail=""):
    """Persist only the outcome actually proved by the executor."""
    operation.state = state
    operation.receipt = receipt or {}
    operation.detail = detail
    if state == "succeeded":
        family = "approval" if operation.action.startswith("approval.") else "proposal"
        operation.receipt_ref = f"{family}:{operation.source_id}"
    else:
        operation.receipt_ref = ""
    operation.save(update_fields=["state", "receipt", "detail", "receipt_ref", "updated_at"])
    return operation


def lookup_operation(*, actor, thread_id, operation_id=None, decision_id=None):
    """Owner/thread/scope-bound reconciliation across a replacement voice session."""
    from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
    from aichat.models import ChatActionProposal
    from voice.models import VoiceOperation

    owner, (_, scope_hash) = ProposalAdapter().owner_scope(actor)
    identity = (
        {"pk": operation_id}
        if operation_id
        else {"decision_id": decision_id}
        if decision_id
        else {}
    )
    operation = (
        VoiceOperation.objects
        .filter(**identity, session__owner=owner, session__thread_id=str(thread_id))
        .order_by("-created_at")
        .first()
    )
    if operation is None:
        return None
    if operation.action.startswith("approval."):
        return _approval_receipt(operation, owner)
    proposal = ChatActionProposal.objects.filter(
        pk=operation.source_id, owner=owner, scope_hash=scope_hash
    ).first()
    if proposal is None:
        return None
    remaining_gates = None
    if proposal.action_type.startswith("closeout."):
        from aichat.services.proposals import _authorized_work_order
        from tasks.services.readiness import evaluate_work_order_readiness

        try:
            work_order = _authorized_work_order(owner, proposal.target_work_order_id)
        except Exception:
            return None
        if proposal.action_type == "closeout.handoff":
            readiness = evaluate_work_order_readiness(work_order, action="complete", actor=owner)
            remaining_gates = [item.message for item in readiness.blockers]
    if proposal.action_type.startswith("stock."):
        from aichat.services.stock_commands import receipt_visible

        if not receipt_visible(proposal, owner):
            return None
    if proposal.state == "executed" and proposal.receipt and verified_work_order_receipt(proposal):
        # The command's immutable event receipt survives a lost HTTP response
        # and a crash between the domain commit and the operation update.
        finish_operation(operation, state="succeeded", receipt=proposal.receipt)
    elif operation.state == "succeeded":
        finish_operation(
            operation, state="unknown", detail="The domain receipt could not be verified."
        )
    return {
        "operation_id": str(operation.pk),
        **({"remaining_gates": remaining_gates} if remaining_gates is not None else {}),
        "source_id": operation.source_id,
        "execution_state": operation.state,
        "target_label": operation.target_label,
        "receipt_ref": operation.receipt_ref or None,
        "receipt": operation.receipt,
        "detail": operation.detail,
    }


def _approval_receipt(operation, owner):
    """Verify effects or immutable decision events; never re-dispatch a request."""
    from approvals.access import visible_approvals
    from approvals.models import Approval, ApprovalEvent, ApprovalExecution

    approval = visible_approvals(
        Approval.objects.filter(pk=operation.source_id), owner, force_scoped=True
    ).first()
    if approval is None:
        return None
    action = operation.action.removeprefix("approval.")
    receipt = operation.receipt or {}
    if action == "approve":
        execution = ApprovalExecution.objects.filter(
            pk=approval.idempotency_key, approval=approval, actor=owner
        ).first()
        if execution is not None:
            state = execution.state
            invalid_receipt = state == "succeeded" and not _verified_approval_effect(
                approval, execution
            )
            if invalid_receipt:
                state = "unknown"
            finish_operation(
                operation,
                state=state,
                receipt=(
                    {}
                    if invalid_receipt
                    else {
                        "approval_id": str(approval.pk),
                        "status": approval.status,
                        "action": action,
                        "execution_result": approval.execution_result,
                    }
                ),
                detail=(
                    "The approval effect receipt could not be verified."
                    if invalid_receipt
                    else execution.detail
                ),
            )
        elif operation.state == "succeeded":
            finish_operation(
                operation, state="unknown", detail="The approval execution could not be verified."
            )
    elif operation.state == "succeeded":
        event_type = {
            "deny": "denied",
            "request_changes": "changes_requested",
            "cancel": "canceled",
        }.get(action)
        if (
            not event_type
            or not ApprovalEvent.objects.filter(
                pk=receipt.get("event_id"),
                approval=approval,
                actor_user=owner,
                event_type=event_type,
            ).exists()
        ):
            finish_operation(
                operation,
                state="unknown",
                detail="The approval decision event could not be verified.",
            )
    return {
        "operation_id": str(operation.pk),
        "source_id": operation.source_id,
        "execution_state": operation.state,
        "target_label": operation.target_label,
        "receipt_ref": operation.receipt_ref or None,
        "receipt": operation.receipt,
        "detail": operation.detail,
    }


def _verified_approval_effect(approval, execution):
    """Require the exact effect key and agreement of both atomic result records."""
    from approvals.executors import compute_effect_idempotency_key
    from approvals.models import ExecutedEffect

    result = execution.result
    if (
        approval.status != "succeeded"
        or execution.revision != approval.current_revision_number
        or not isinstance(result, dict)
        or not isinstance(result.get("payload"), dict)
        or not result.get("effect_ref")
        or approval.execution_result
        != {
            **result["payload"],
            "execution_state": "succeeded",
            "operation_id": execution.pk,
            "effect_ref": result["effect_ref"],
        }
    ):
        return False
    return ExecutedEffect.objects.filter(
        pk=compute_effect_idempotency_key(approval.idempotency_key, approval.action_type),
        approval=approval,
        effect_type=approval.action_type,
        effect_ref=result["effect_ref"],
    ).exists()


def verified_hold_receipt(proposal):
    """Compatibility helper for the Phase B hold-only receipt contract."""
    return proposal.action_type == "work_order.hold" and verified_work_order_receipt(proposal)


def verified_work_order_receipt(proposal):
    """Cross-check supported receipts against the exact canonical command/event."""
    if proposal.action_type.startswith("closeout."):
        from ai.core.decisions.adapters.capture_adapter import verified

        return verified(proposal)
    if proposal.action_type == "procedure.complete":
        from aichat.services.procedure_commands import verified

        return verified(proposal)
    if proposal.action_type.startswith("stock."):
        from aichat.services.stock_commands import verified

        return verified(proposal)
    from tasks.models import WorkOrderCommand, WorkOrderDeletionRecord, WorkOrderEvent

    receipt = proposal.receipt or {}
    if proposal.action_type == "work_order.delete":
        return WorkOrderDeletionRecord.objects.filter(
            pk=receipt.get("deletion_record_id"),
            work_order_pk=proposal.target_work_order_id,
            actor_id=proposal.owner_id,
            idempotency_key=f"proposal:{proposal.pk}",
            correlation_id=receipt.get("correlation_id"),
        ).exists()

    expected = {
        "work_order.hold": ("hold", "HOLD", "on_hold", True),
        "work_order.cancel": ("cancel", "CANCEL", "canceled", True),
        "work_order.resume": ("resume", "RESUME", "in_progress", True),
        "work_order.assign": ("assign", "ASSIGNED", None, True),
        "work_order.schedule": ("schedule", "SCHEDULED", None, True),
        "work_order.resize": ("resize", "RESIZED", None, True),
        "work_order.update": ("update_plan", "PLAN_UPDATED", None, True),
        "work_order.transition": ("transition", "TRANSITION", None, True),
        "work_order.create_child": ("create_child", "CARD_CREATED", None, False),
        "work_order.generate_procurement": (
            "generate_procurement",
            "PROPOSAL_RECORDED",
            None,
            False,
        ),
        "dependency.create": ("create_dependency", "PROPOSAL_RECORDED", None, False),
        "dependency.delete": ("delete_dependency", "PROPOSAL_RECORDED", None, False),
    }.get(proposal.action_type)
    receipt = proposal.receipt or {}
    if expected is None or not isinstance(receipt, dict):
        return False
    name, event_type, status, versioned = expected
    if (
        receipt.get("command") != name
        or (status is not None and receipt.get("lifecycle_status") != status)
        or receipt.get("work_order_id") != proposal.target_work_order_id
        or receipt.get("idempotency_key") != f"proposal:{proposal.pk}"
        or (versioned and receipt.get("lifecycle_version") != proposal.target_version + 1)
    ):
        return False
    command = WorkOrderCommand.objects.filter(
        work_order_id=proposal.target_work_order_id,
        command=name,
        status="succeeded",
        idempotency_key=receipt.get("idempotency_key"),
    ).first()
    if (
        command is None
        or str(command.result_ref) != str(receipt.get("event_id"))
        or str(command.correlation_id) != str(receipt.get("correlation_id"))
    ):
        return False
    filters = {"to_status": receipt.get("lifecycle_status")} if versioned else {}
    return WorkOrderEvent.objects.filter(
        pk=receipt.get("event_id"),
        work_order_id=proposal.target_work_order_id,
        actor_id=proposal.owner_id,
        event_type=event_type,
        idempotency_key=command.idempotency_key,
        correlation_id=command.correlation_id,
        **filters,
    ).exists()


def recover_latest(*, actor, thread_id, session_id, now):
    """Reconstruct a receipt-only view after cache loss, never a confirmable intent."""
    from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
    from ai.core.decisions.models import PendingDecision
    from aichat.models import ChatActionProposal
    from aichat.services.proposals import ProposalError
    from voice.models import VoiceOperation

    operation = (
        VoiceOperation.objects
        .filter(session__owner_id=actor.user_pk, session__thread_id=str(thread_id))
        .order_by("-created_at")
        .first()
    )
    if operation is None:
        return None
    try:
        owner, (_, scope_hash) = ProposalAdapter().owner_scope(actor)
    except ProposalError:
        return None
    result = lookup_operation(actor=actor, thread_id=thread_id, operation_id=operation.pk)
    if result is None:
        return None
    if operation.action.startswith("approval."):
        from approvals.models import Approval
        from approvals.review_sections import compute_review_hash

        source = Approval.objects.get(pk=operation.source_id)
        revision, preview_hash, kind = (
            source.current_revision_number,
            compute_review_hash(source),
            "approval_decision",
        )
    else:
        source = ChatActionProposal.objects.get(
            pk=operation.source_id, owner=owner, scope_hash=scope_hash
        )
        revision, preview_hash, kind = source.target_version or 0, source.preview_hash, "action"
    spoken = spoken_receipt(result)
    return PendingDecision(
        decision_id="recovered:" + operation.decision_id,
        kind=kind,
        source_id=operation.source_id,
        revision=revision,
        state="resolved",
        target_label=operation.target_label,
        sequence=1,
        expires_at=now,
        armed_at=now,
        actor_user_pk=str(owner.pk),
        session_id=str(session_id),
        thread_id=str(thread_id),
        scope_hash=scope_hash,
        nonce=str(operation.pk),
        source_content="Receipt recovery only; never execute.",
        allowed_responses=("repeat", "what changed"),
        locale="en-US",
        preview_hash=preview_hash,
        operation_id=str(operation.pk),
        execution_state=result["execution_state"],
        receipt_ref=result["receipt_ref"],
        spoken_summary=spoken,
    )


def spoken_receipt(result):
    """Use business-result vocabulary, not HTTP success or assistant assertions."""
    from ai.core.decisions.pronunciation import spoken_target

    if result and result["execution_state"] == "succeeded":
        if result["receipt"].get("approval_id"):
            status = str(result["receipt"].get("status", "recorded")).replace("_", " ")
            execution = result["receipt"].get("execution_result") or {}
            suffix = (
                f" Order {execution.get('order_reference') or execution.get('order_id')} recorded."
                if execution.get("order_id")
                else ""
            )
            return f"{result['target_label']}: {status}. The result is recorded.{suffix}"
        receipt = result["receipt"]
        label = spoken_target(result["target_label"])
        command = receipt.get("command")
        if command == "closeout.consent":
            return "Consent recorded. Say note followed by your dictation. No closeout note has been handed off."
        if command == "closeout.accept":
            return "The exact note revision is accepted. No handoff was submitted. Say handoff this note for a separate confirmation."
        if command == "closeout.handoff":
            gates = result.get("remaining_gates")
            detail = (
                " Remaining gates: " + "; ".join(gates) + "."
                if gates
                else " Request a fresh readiness review before attempting completion."
            )
            return (
                "The accepted note was handed off to closeout review. The work order is not closed."
                + detail
            )
        if command == "complete_step":
            return f"{label}: procedure step {receipt['step_key']} recorded as {receipt['status']}. The work order is not automatically closed."
        if str(command).startswith("stock."):
            return (
                f"{label}: inventory {str(command).removeprefix('stock.')} recorded. "
                f"Quantity before {receipt['quantity_before']}; quantity after {receipt['quantity_after']} "
                f"{receipt['units']}. Tracking reference {receipt['tracking_ref']}."
            )
        if command in ("hold", "resume", "cancel", "transition"):
            status = str(receipt.get("lifecycle_status") or "").replace("_", " ")
            return f"{label} is now {status}. The change is recorded."
        changes = {
            "assign": "assignment updated",
            "schedule": "schedule updated",
            "resize": "duration updated",
            "update_plan": "plan updated",
            "delete": "deleted",
            "create_child": "child card recorded",
            "create_dependency": "dependency recorded",
            "delete_dependency": "dependency removed",
            "generate_procurement": "procurement card recorded"
            if receipt.get("child_id")
            else "no procurement card was needed",
        }
        return f"{label}: {changes.get(command, 'result recorded')}. The result is recorded."
    if result and result["execution_state"] == "failed_before_effect":
        return f"{spoken_target(result['target_label'])}: the change was not applied. {result.get('detail', '')}"
    return (
        "The result is not yet verified. I am reconciling the recorded operation; do not retry it."
    )
