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
    if proposal.state == "executed" and proposal.receipt and verified_hold_receipt(proposal):
        # The command's immutable event receipt survives a lost HTTP response
        # and a crash between the domain commit and the operation update.
        finish_operation(operation, state="succeeded", receipt=proposal.receipt)
    elif operation.state == "succeeded":
        finish_operation(
            operation, state="unknown", detail="The domain receipt could not be verified."
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


def _approval_receipt(operation, owner):
    """Verify effects or immutable decision events; never re-dispatch a request."""
    from approvals.access import visible_approvals
    from approvals.models import Approval, ApprovalEvent, ApprovalExecution, ExecutedEffect

    approval = visible_approvals(
        Approval.objects.filter(pk=operation.source_id), owner, force_scoped=True
    ).first()
    if approval is None:
        return None
    action = operation.action.removeprefix("approval.")
    receipt = operation.receipt or {}
    if action == "approve":
        execution = ApprovalExecution.objects.filter(approval=approval, actor=owner).first()
        if execution is not None:
            state = execution.state
            if (
                state == "succeeded"
                and not ExecutedEffect.objects.filter(approval=approval).exists()
            ):
                state = "unknown"
            finish_operation(
                operation,
                state=state,
                receipt={
                    "approval_id": str(approval.pk),
                    "status": approval.status,
                    "action": action,
                    "execution_result": approval.execution_result,
                },
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


def verified_hold_receipt(proposal):
    """Cross-check the Phase B receipt against the domain's command and audit event."""
    from tasks.models import WorkOrderCommand, WorkOrderEvent

    receipt = proposal.receipt
    if proposal.action_type != "work_order.hold" or receipt.get("command") != "hold":
        return False
    command = WorkOrderCommand.objects.filter(
        work_order_id=proposal.target_work_order_id,
        command="hold",
        status="succeeded",
        idempotency_key=receipt.get("idempotency_key"),
    ).first()
    if command is None or str(command.result_ref) != str(receipt.get("event_id")):
        return False
    return WorkOrderEvent.objects.filter(
        pk=receipt.get("event_id"),
        work_order_id=proposal.target_work_order_id,
        idempotency_key=command.idempotency_key,
        correlation_id=command.correlation_id,
        to_status=receipt.get("lifecycle_status"),
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
        status = str(result["receipt"].get("lifecycle_status") or "").replace("_", " ")
        return f"{spoken_target(result['target_label'])} is now {status}. The change is recorded."
    if result and result["execution_state"] == "failed_before_effect":
        return f"{spoken_target(result['target_label'])}: the change was not applied. {result.get('detail', '')}"
    return (
        "The result is not yet verified. I am reconciling the recorded operation; do not retry it."
    )
