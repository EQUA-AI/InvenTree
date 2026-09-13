"""Fail-closed action inventory: holds and explicitly enabled purchasing reviews."""

from ai.core.decisions.adapters.base import ActionSpec

HOLD = ActionSpec(
    action="work_order.hold",
    target_type="WorkOrder",
    command_and_authorization="hold_work_order; EXECUTE_WORKORDER; current actor maintenance scope",
    preview_fields=(
        "reference",
        "title",
        "current_status",
        "resulting_status",
        "reason",
        "warning",
    ),
    confirmation="confirm hold or explicit short assent",
    version_idempotency_receipt="target_version; owner/proposal key; WorkOrderCommand and WorkOrderEvent",
    compensation_and_failure="Separate confirmed resume; reconcile unknown effects by proposal receipt",
    available=True,
    unavailable_reason="",
)


def action_spec(action: str) -> ActionSpec:
    """Return an explicit unavailable entry for every unmapped action."""
    if action == HOLD.action:
        return HOLD
    from ai.core.decisions.adapters.purchasing_adapter import OPERATIONS

    if action in OPERATIONS:
        from ai.core.config import get_settings

        available = bool(get_settings().feature_voice_external_actions)
        return ActionSpec(
            action=action,
            target_type="PurchaseOrder",
            command_and_authorization="approvals.purchasing.execute; fresh purchasing role and assigned scoped reviewer",
            preview_fields=(
                "operation",
                "supplier_name",
                "order_reference",
                "line_items",
                "total",
                "currency",
                "description",
            ),
            confirmation="complete revision-bound review, then strict approve <request ref>; bare yes refused",
            version_idempotency_receipt="review hash and order baseline; ApprovalExecution and ExecutedEffect; order and line IDs",
            compensation_and_failure="No automatic resend; DB-only atomic rollback; separate reviewed cancellation on screen",
            available=available,
            unavailable_reason="" if available else "Purchasing voice actions are disabled.",
        )
    return ActionSpec(
        action=action,
        target_type="unavailable",
        command_and_authorization="unavailable",
        preview_fields=(),
        confirmation="unavailable",
        version_idempotency_receipt="unavailable",
        compensation_and_failure="No voice execution; no effect submitted",
    )
