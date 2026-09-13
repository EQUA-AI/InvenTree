"""Bounded purchasing intents create reviews, never purchase-order effects.

No text-tool executor is invoked. The three supported actions converge on the
canonical purchasing approval service, strict confirmation and durable receipt.
"""

import re

from ai.core.decisions.coordinator import DecisionConflict, DecisionReply

OPERATIONS = ("create_purchase_order", "add_po_line_item", "issue_purchase_order")
HELP = (
    "For a draft, say create purchase order for supplier ID in USD, description, then the description. "
    "For a line, say add supplier part ID to purchase order REFERENCE, quantity NUMBER, price NUMBER USD. "
    "To issue a reviewed draft, say issue purchase order REFERENCE. No action has been submitted."
)


def parse(content):
    """Require explicit IDs, quantity, price and currency; never infer assent."""
    text = content.strip().rstrip(".!?")
    create = re.fullmatch(
        r"(?:please )?create (?:a )?(?:draft )?purchase order for supplier (?:id )?(\d+) in ([a-z]{3})[, ]+description[, :]+(.+)",
        text,
        re.I,
    )
    if create:
        return {
            "operation": "create_purchase_order",
            "supplier_id": int(create[1]),
            "currency": create[2].upper(),
            "description": create[3],
            "line_items": [],
        }
    line = re.fullmatch(
        r"(?:please )?add supplier part (?:id )?(\d+) to purchase order ([a-z0-9-]+)[, ]+quantity (\d+(?:\.\d+)?)[, ]+price (\d+(?:\.\d+)?) ([a-z]{3})",
        text,
        re.I,
    )
    if line:
        return {
            "operation": "add_po_line_item",
            "order_reference": line[2],
            "currency": line[5].upper(),
            "line_items": [
                {"supplier_part_id": int(line[1]), "quantity": line[3], "unit_price": line[4]}
            ],
        }
    issue = re.fullmatch(r"(?:please )?issue purchase order ([a-z0-9-]+)", text, re.I)
    if issue:
        return {"operation": "issue_purchase_order", "order_reference": issue[1]}
    return None


def begin(c, adapter, content, **arguments):
    """Validate scope and permissions before preparing an assigned review."""
    from ai.core.config import get_settings
    from approvals import purchasing
    from approvals.access import has_purchase_view
    from approvals.serializers import ApprovalCreateSerializer
    from order.models import PurchaseOrder

    payload = parse(content)
    if payload is None:
        if re.match(r"^(?:please )?(?:create|add|issue)\b", content.strip(), re.I) and re.search(
            r"\bpurchase order\b", content, re.I
        ):
            return DecisionReply(HELP, event="clarification")
        return None
    if not get_settings().feature_voice_external_actions:
        return DecisionReply(
            "Purchasing voice actions are disabled. Review this on screen.", event="ineligible"
        )
    owner, _ = adapter.owner(arguments["actor"])
    if not has_purchase_view(owner):
        raise DecisionConflict("Purchasing access is required.")
    purchasing._actor(owner, payload["operation"])
    if "order_reference" in payload:
        orders = list(
            PurchaseOrder.objects.filter(reference__iexact=payload.pop("order_reference"))[:2]
        )
        if len(orders) != 1:
            return DecisionReply(
                "Name one exact purchase order reference from your purchasing screen.",
                event="refused",
            )
        payload["order_id"] = orders[0].pk
        if payload["operation"] == "issue_purchase_order":
            payload["currency"] = orders[0].order_currency
    expected = c.store.read(arguments["thread_id"])
    serializer = ApprovalCreateSerializer(
        data={
            "action_type": "purchase_order",
            "payload": payload,
            "summary": f"{payload['operation'].replace('_', ' ')}: {payload.get('description') or payload.get('order_id')}",
            "assigned_to_user_id": owner.pk,
            "source_chat_id": str(arguments["thread_id"]),
            "agent_run_id": f"voice:{owner.pk}:{arguments['session_id']}",
            "agent_checkpoint_id": str(arguments["nonce"]),
            "tool_call_id": str(arguments["nonce"]),
        }
    )
    serializer.is_valid(raise_exception=True)
    approval = serializer.save()
    if not adapter.queryset(owner).filter(pk=approval.pk).exists():
        raise DecisionConflict("The purchasing review is unavailable to you.")
    if approval.status not in ("pending", "in_review", "changes_requested"):
        return DecisionReply(
            "This turn already has a submitted request. Check its recorded status.", event="refused"
        )
    return adapter.review(c, approval, expected=expected, **arguments)
