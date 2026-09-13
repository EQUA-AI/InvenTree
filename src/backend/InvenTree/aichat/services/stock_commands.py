"""Governed in-process stock commands shared by proposals and approvals.

The reviewed snapshot, canonical StockItem method, retained tracking entry and
idempotent receipt commit in one transaction. No REST calls or implicit retries.
"""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from aichat.services.proposals import (
    ProposalError,
    ProposalPreviewChanged,
    compute_preview_hash,
)

STOCK_ACTIONS = frozenset({
    'stock.add',
    'stock.remove',
    'stock.transfer',
    'stock.count',
})


def require_role(actor, permission='change'):
    """Read live grants, not a transcript identity or cached permission result."""
    actor = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if actor is None or not (
        actor.is_superuser
        or actor.groups.filter(
            rule_sets__name='stock', **{f'rule_sets__can_{permission}': True}
        ).exists()
    ):
        raise ProposalError(f'Stock {permission} permission is required.')
    return actor


def quantity(value, *, zero=False):
    """Keep exact decimals within the canonical quantity field's precision."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProposalError('Supply an exact finite quantity.') from exc
    if (
        not number.is_finite()
        or number < 0
        or (not zero and number == 0)
        or number >= Decimal('10000000000')
        or number.as_tuple().exponent < -5
    ):
        raise ProposalError(
            'Supply a nonnegative quantity with at most five decimal places; movement quantities must be positive.'
        )
    return number


def location_snapshot(location):
    """Name every ancestor so an identically named shelf cannot be substituted."""
    return (
        None
        if location is None
        else {
            'id': location.pk,
            'path': location.pathstring,
            'name': location.name,
            'structural': location.structural,
            'owner_id': location.owner_id,
        }
    )


def _location(pk, actor):
    from stock.models import StockLocation

    location = StockLocation.objects.filter(pk=pk).first()
    if location is None or (actor is not None and not location.check_ownership(actor)):
        raise ProposalError('That stock location is unavailable in your current scope.')
    if location.structural:
        raise ProposalError(
            'Choose a stock-holding location, not a structural location.'
        )
    return location


def prepare(*, actor, action, intent, authorize=True):
    """Resolve canonical identities and all effect-relevant preview fields."""
    from part.models import Part
    from stock.models import StockItem

    if action not in STOCK_ACTIONS:
        raise ProposalError('That inventory movement is not available by voice.')
    if set(intent) - {'stock_item_id', 'part_id', 'location_id', 'quantity', 'reason'}:
        raise ProposalError('Unsupported stock parameters; request a fresh preview.')
    # Internal approval drift checks may read the snapshot without an actor;
    # creation and execution ALWAYS use the default authorized mode.
    actor = require_role(actor) if authorize else None
    amount = quantity(intent.get('quantity'), zero=action == 'stock.count')
    reason = str(intent.get('reason') or '').strip()
    if len(reason) > 2000:
        raise ProposalError('The stock reason is too long; review it on screen.')
    item = None
    target_id = intent.get('stock_item_id')
    if target_id is not None:
        item = (
            StockItem.objects
            .select_related('part', 'location')
            .filter(pk=target_id)
            .first()
        )
        if item is None or (actor is not None and not item.check_ownership(actor)):
            raise ProposalError('That stock item is unavailable in your current scope.')
        if not item.is_in_stock() or item.is_building:
            raise ProposalError(
                'Only available, unallocated stock can be adjusted by voice.'
            )
        if item.allocation_count():
            raise ProposalError('Allocated stock requires on-screen review.')
        part = item.part
        if intent.get('part_id') not in (None, part.pk):
            raise ProposalError('The reviewed part and stock item do not match.')
        if item.serialized and (action in ('stock.add', 'stock.remove') or amount != 1):
            raise ProposalError(
                'Serialized stock can only be transferred or counted as one by voice.'
            )
        before = item.quantity
        if action in ('stock.remove', 'stock.transfer') and amount > before:
            raise ProposalError(
                'The requested movement exceeds the available stock; nothing was changed.'
            )
        destination = (
            _location(intent.get('location_id'), actor)
            if action == 'stock.transfer'
            else item.location
        )
        if action != 'stock.transfer' and intent.get('location_id') not in (
            None,
            item.location_id,
        ):
            raise ProposalError('This movement cannot also change the location.')
        if action == 'stock.transfer' and destination.pk == item.location_id:
            raise ProposalError('Choose a different destination for the transfer.')
    else:
        if action != 'stock.add':
            raise ProposalError('This movement requires an existing stock item.')
        if authorize:
            require_role(actor, 'add')
        part = Part.objects.filter(
            pk=intent.get('part_id'), active=True, virtual=False
        ).first()
        if part is None or part.trackable:
            raise ProposalError(
                'Choose a non-serialized active part for a new stock addition.'
            )
        destination = _location(intent.get('location_id'), actor)
        before = Decimal(0)
    after = {
        'stock.add': before + amount,
        'stock.remove': before - amount,
        'stock.count': amount,
        'stock.transfer': before if amount == before else before - amount,
    }[action]
    quantity(after, zero=True)
    return {
        'action_type': action,
        'stock_item_id': target_id,
        'part_id': part.pk,
        'part_name': part.name,
        'IPN': part.IPN,
        'units': part.units or 'each',
        'serial': item.serial if item else '',
        'quantity': str(amount),
        'quantity_before': str(before),
        'quantity_after': str(after),
        'location': location_snapshot(item.location) if item else None,
        'destination': location_snapshot(destination),
        'updated': item.updated.isoformat() if item and item.updated else None,
        'status': item.status if item else None,
        'owner_id': item.owner_id if item else None,
        'delete_on_deplete': item.delete_on_deplete if item else False,
        'reason': reason,
        'irreversible': action == 'stock.remove',
        'confirm_phrase': 'confirm remove' if action == 'stock.remove' else '',
        'warning': 'Only this inventory record changes. No purchase, work-order completion or notification is performed.',
    }


@transaction.atomic
def execute(*, actor, action, intent, preview, idempotency_key):
    """One atomic domain effect with an immutable proposal/approval-specific key."""
    from aichat.models import StockCommandReceipt
    from InvenTree.status_codes import StockHistoryCode
    from part.models import Part
    from plugin.events import batch_events
    from stock.models import StockItem, StockItemTracking, StockLocation

    actor = require_role(actor)
    request_hash = compute_preview_hash({
        'action': action,
        'intent': intent,
        'preview': preview,
    })
    existing = StockCommandReceipt.objects.filter(
        actor=actor, idempotency_key=idempotency_key
    ).first()
    if existing:
        if existing.request_hash != request_hash:
            raise ProposalError(
                'This stock command key belongs to a different reviewed action.'
            )
        return dict(existing.receipt)
    # Unique key acquisition precedes the effect. The inner savepoint lets a
    # concurrent committed winner be read without poisoning the outer transaction.
    try:
        with transaction.atomic():
            command = StockCommandReceipt.objects.create(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                receipt={},
            )
    except IntegrityError:
        existing = StockCommandReceipt.objects.get(
            actor=actor, idempotency_key=idempotency_key
        )
        if existing.request_hash != request_hash:
            raise ProposalError(
                'This stock command key belongs to another action.'
            ) from None
        return dict(existing.receipt)
    # Serialize mutations of the part and destination as well as the item.
    list(Part.objects.select_for_update().filter(pk=preview['part_id']))
    locations = [
        value['id']
        for value in (preview.get('location'), preview.get('destination'))
        if value
    ]
    list(
        StockLocation.objects
        .select_for_update()
        .filter(pk__in=locations)
        .order_by('pk')
    )
    item = (
        StockItem.objects
        .select_for_update()
        .filter(pk=intent.get('stock_item_id'))
        .first()
    )
    fresh = prepare(actor=actor, action=action, intent=intent)
    if compute_preview_hash(fresh) != compute_preview_hash(preview):
        raise ProposalPreviewChanged(
            'Stock or destination changed. Request a fresh inventory preview.'
        )
    amount = quantity(intent['quantity'], zero=action == 'stock.count')
    note = f'Governed stock command {command.pk}. {fresh["reason"]}'
    destination = (
        StockLocation.objects.get(pk=fresh['destination']['id'])
        if fresh['destination']
        else None
    )
    original_id = item.pk if item else None
    moved_id = None
    with batch_events():
        if item is None:
            item = StockItem(
                part_id=fresh['part_id'], location=destination, quantity=amount
            )
            item.full_clean()
            item.save(user=actor)
            original_id = item.pk
            item.add_tracking_entry(
                StockHistoryCode.STOCK_ADD,
                actor,
                notes=note,
                deltas={'added': str(amount), 'quantity': str(amount)},
            )
        elif action == 'stock.add':
            if not item.add_stock(amount, actor, notes=note):
                raise ProposalError('Stock addition was refused; nothing was changed.')
        elif action == 'stock.remove':
            if not item.take_stock(amount, actor, notes=note):
                raise ProposalError('Stock removal was refused; nothing was changed.')
        elif action == 'stock.count':
            item.stocktake(amount, actor, notes=note)
        elif amount < item.quantity:
            moved = item.splitStock(
                amount, location=destination, user=actor, notes=note
            )
            if moved is None:
                raise ProposalError('Stock transfer was refused; nothing was changed.')
            moved_id = moved.pk
        elif not item.move(destination, note, actor, quantity=amount):
            raise ProposalError('Stock transfer was refused; nothing was changed.')
        tracking = list(
            StockItemTracking.objects.filter(user=actor, notes=note).order_by('pk')
        )
        if not tracking:
            raise ProposalError(
                'The stock command did not produce a tracking receipt; nothing was committed.'
            )
        actual = (
            StockItem.objects
            .filter(pk=original_id)
            .values_list('quantity', flat=True)
            .first()
        )
        if (actual or Decimal(0)) != Decimal(fresh['quantity_after']):
            raise ProposalError(
                'The stock result differed from the review; nothing was committed.'
            )
        receipt = {
            'command': action,
            'stock_command_id': str(command.pk),
            'stock_item_id': original_id,
            'moved_stock_item_id': moved_id,
            'quantity_before': fresh['quantity_before'],
            'quantity_after': fresh['quantity_after'],
            'quantity_moved': str(amount) if action == 'stock.transfer' else None,
            'location_id': destination.pk if destination else None,
            'tracking_ref': tracking[-1].pk,
            'tracking_refs': [row.pk for row in tracking],
            'units': fresh['units'],
            'part_id': fresh['part_id'],
            'depleted': actual is None,
            'idempotency_key': idempotency_key,
        }
        command.receipt = receipt
        command.save(update_fields=['receipt'])
    return receipt


def verified(proposal):
    """Retained command and tracking evidence survive deletion of depleted stock."""
    from aichat.models import StockCommandReceipt
    from stock.models import StockItemTracking

    receipt = proposal.receipt
    if not isinstance(receipt, dict) or not receipt.get('tracking_refs'):
        return False
    command = StockCommandReceipt.objects.filter(
        actor_id=proposal.owner_id,
        action=proposal.action_type,
        idempotency_key=f'proposal:{proposal.pk}',
    ).first()
    return bool(
        command
        and command.request_hash
        == compute_preview_hash({
            'action': proposal.action_type,
            'intent': proposal.intent,
            'preview': proposal.preview,
        })
        and command.receipt == receipt
        and StockItemTracking.objects.filter(
            pk__in=receipt['tracking_refs'],
            user_id=proposal.owner_id,
            notes__startswith=f'Governed stock command {command.pk}. ',
        ).count()
        == len(receipt['tracking_refs'])
    )


def receipt_visible(proposal, actor):
    """Keep current inventory ownership even when the original stock was depleted."""
    from types import SimpleNamespace

    from approvals.inventory import visible

    return visible(
        SimpleNamespace(
            payload={'intent': proposal.intent, 'snapshot': proposal.preview},
            is_terminal=proposal.is_terminal,
        ),
        actor,
    )
