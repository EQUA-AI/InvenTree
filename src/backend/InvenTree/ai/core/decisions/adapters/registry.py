"""Fail-closed inventory of implemented decisions and enabled purchasing reviews."""

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


CANCEL = ActionSpec(
    action="work_order.cancel",
    target_type="WorkOrder",
    command_and_authorization="cancel_work_order; TRANSITION_WORKORDER; current actor maintenance scope",
    preview_fields=(*HOLD.preview_fields, "irreversible", "confirm_phrase"),
    confirmation="confirm cancel order; optional matching reference; say no to decline",
    version_idempotency_receipt=HOLD.version_idempotency_receipt,
    compensation_and_failure="No compensation; reconcile unknown effects by proposal receipt",
    available=True,
    unavailable_reason="",
)


def action_spec(action: str) -> ActionSpec:
    """Return an explicit unavailable entry for every unmapped action."""
    if action in (HOLD.action, CANCEL.action):
        return HOLD if action == HOLD.action else CANCEL
    if action in {"stock.add", "stock.remove", "stock.transfer", "stock.count"}:
        from ai.core.config import get_settings

        available = get_settings().feature_voice_inventory_actions
        return ActionSpec(
            action=action,
            target_type="StockItem",
            command_and_authorization="Canonical stock command; fresh inventory roles and ownership",
            preview_fields=(
                "part_name",
                "part_ipn",
                "stock_item_id",
                "serial",
                "units",
                "quantity_before",
                "quantity_after",
                "quantity",
                "source",
                "destination",
                "reason",
            ),
            confirmation="Full read-back; confirm remove for removal; explicit assent otherwise",
            version_idempotency_receipt="Locked snapshot/hash; StockCommandReceipt and StockItemTracking",
            compensation_and_failure="No automatic retry or compensation; separate reviewed command",
            available=available,
            unavailable_reason="" if available else "Inventory actions by voice are disabled.",
        )
    from ai.core.decisions.work_order_review import FIELDS

    if action in FIELDS:
        from ai.core.config import get_settings

        available = (
            action != "procedure.complete" or get_settings().feature_voice_procedure_complete
        )
        if action.startswith("closeout."):
            available = getattr(get_settings(), "feature_voice_closeout", False)
        return ActionSpec(
            action=action,
            target_type="WorkOrder",
            command_and_authorization="Canonical tasks service; fresh permission and maintenance scope",
            preview_fields=FIELDS[action],
            confirmation="Full read-back; strict phrase when irreversible",
            version_idempotency_receipt="Fresh preview hash/version; atomic proposal receipt and canonical audit",
            compensation_and_failure="Separate reviewed command only; no automatic retry",
            available=available,
            unavailable_reason="" if available else "Procedure completion by voice is disabled.",
        )
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
