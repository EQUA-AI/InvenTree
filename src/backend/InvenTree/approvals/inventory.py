"""Canonical stock approval contract; inventory commands own the atomic effect."""

from aichat.services import stock_commands


def prepare_payload(payload, *, actor):
    """Rebuild every reviewed stock field using authenticated current permissions."""
    if not isinstance(payload, dict) or set(payload) - {'action', 'intent', 'snapshot'}:
        raise ValueError('Stock approval requires action and intent only.')
    action = payload.get('action')
    intent = dict(payload.get('intent') or {})
    snapshot = stock_commands.prepare(actor=actor, action=action, intent=intent)
    return {'action': action, 'intent': intent, 'snapshot': snapshot}


def baseline(payload):
    """Internal comparison only; no permission or effect is granted by this read."""
    return stock_commands.prepare(
        actor=None, action=payload['action'], intent=payload['intent'], authorize=False
    )


def validate(payload):
    """Reject stale, incomplete or legacy stub payloads before voice review."""
    try:
        if (
            set(payload) != {'action', 'intent', 'snapshot'}
            or baseline(payload) != payload['snapshot']
        ):
            return [
                'Inventory changed or the review is incomplete. Request a new review.'
            ]
    except Exception:
        return ['The inventory action is unavailable. Request a new review.']
    return []


def visible(approval, actor):
    """Actual stock ownership, including retained location scope after depletion."""
    from stock.models import StockItem, StockLocation

    try:
        stock_commands.require_role(actor, 'view')
        payload = approval.payload
        item_id = payload['intent'].get('stock_item_id')
        if item_id:
            item = StockItem.objects.filter(pk=item_id).first()
            if item and not item.check_ownership(actor):
                return False
            if item is None and not approval.is_terminal:
                return False
        for key in ('location', 'destination'):
            snapshot = payload['snapshot'].get(key)
            if snapshot:
                location = StockLocation.objects.filter(pk=snapshot['id']).first()
                if location is None or not location.check_ownership(actor):
                    return False
        return True
    except (KeyError, TypeError, ValueError):
        return False
    except stock_commands.ProposalError:
        return False
