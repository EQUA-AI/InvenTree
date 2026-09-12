"""Fail-closed action inventory. Phase B enables only the hold vertical slice."""

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
    return ActionSpec(
        action=action,
        target_type="unavailable",
        command_and_authorization="unavailable",
        preview_fields=(),
        confirmation="unavailable",
        version_idempotency_receipt="unavailable",
        compensation_and_failure="No voice execution; no effect submitted",
    )
