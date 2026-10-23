"""Read-only procurement projection for Materials, native PO context and Closeout.

Only exact receipt-event associations disclose stock. A shared Part or PO is
never used to guess receipt ownership. No model saves, stock mutations or
continuations belong in this module.
"""

from decimal import Decimal

from django.utils import timezone

from djmoney.contrib.exchange.exceptions import MissingRate
from djmoney.contrib.exchange.models import convert_money

from tasks.jobkit_models import JobKit
from tasks.models import WorkOrderCommand
from tasks.services.procurement import (
    EMAIL_RECORDED,
    PO_ACKNOWLEDGED,
    PO_LINKED,
    PROCUREMENT_EVENT_TYPES,
    RECEIPT_RECORDED,
    SCHEMA_VERSION,
    SOURCE_SELECTED,
    can_read_procurement,
    configuration_revision,
    event_matches_context,
    purchase_order_revision,
    requirement_revision,
    selection_is_current,
)


def event_fact(event):
    """Retain physical/source time separately from immutable ingestion time."""
    recorded_at = event.created_at
    if timezone.is_naive(recorded_at):
        recorded_at = timezone.make_aware(recorded_at, timezone.get_default_timezone())
    return {
        'event_id': event.pk,
        'operation_key': event.idempotency_key,
        'event_type': event.event_type,
        'source_at': event.metadata.get('source_at'),
        'recorded_at': recorded_at.isoformat(),
        'actor': event.metadata.get('actor')
        or {
            'pk': event.actor_id,
            'label': (event.actor.get_full_name() or event.actor.username)
            if event.actor
            else None,
            'kind': 'internal_person' if event.actor_id else None,
        },
        'snapshot': event.metadata,
    }


def _total(lines, currency):
    """Sum native goods/extra lines with native conversions and discounts.

    Missing prices or FX make a total unknown, rather than falsely zero.
    Decimal arithmetic preserves zero, fractional quantities and freight once.
    """
    total = Decimal('0')
    for line in lines:
        if line.price is None:
            return None
        try:
            price = convert_money(line.price, currency)
        except MissingRate:
            return None
        total += line.quantity * price.amount * (1 - line.discount / 100)
    return str(total)


def _bound(events, event_type, line_id, po_id):
    return [
        event
        for event in events
        if event.event_type == event_type
        and event.metadata.get('purchase_order_line_id') == line_id
        and event.metadata.get('purchase_order_id') == po_id
    ]


def _order(po_line, events, selection=None):
    po = po_line.order
    currency = str(po.currency)
    goods = _total(po.lines.all(), currency)
    charges = _total(po.extra_lines.all(), currency)
    emails = _bound(events, EMAIL_RECORDED, po_line.pk, po.pk)
    acknowledgments = _bound(events, PO_ACKNOWLEDGED, po_line.pk, po.pk)
    ack = acknowledgments[-1] if acknowledgments else None
    bindings = _bound(events, PO_LINKED, po_line.pk, po.pk)
    offers = selection.metadata.get('offers', []) if selection else []
    selected_offer = next(
        (
            offer
            for offer in offers
            if offer.get('key') == selection.metadata.get('selected_offer_key')
            and offer.get('supplier_part_id') == po_line.part_id
        ),
        None,
    )
    return {
        'purchase_order_id': po.pk,
        'reference': po.reference,
        'status': po.status,
        'status_label': po.status_text,
        'supplier_id': po.supplier_id,
        'purchase_order_line_id': po_line.pk,
        'supplier_part_id': po_line.part_id,
        'order_quantity': str(po_line.quantity),
        'received_quantity': str(po_line.received),
        'order_uom': selected_offer.get('order_uom') if selected_offer else None,
        'packaging': po_line.part.packaging if po_line.part else None,
        'base_uom': po_line.part.part.units if po_line.part else None,
        'base_units_per_order_unit': str(po_line.part.pack_quantity_native)
        if po_line.part
        else None,
        'currency': currency,
        'goods_total': goods,
        'charges_total': charges,
        'order_total': str(Decimal(goods) + Decimal(charges))
        if goods is not None and charges is not None
        else None,
        'native_recorded_total': str(po.total_price.amount)
        if po.total_price is not None
        else None,
        'extra_line_ids': [extra.pk for extra in po.extra_lines.all()],
        # Native placement only records a date: no fabricated instant/placer.
        'placed': {
            'date': po.issue_date.isoformat(),
            'precision': 'date',
            'actor': None,
        }
        if po.issue_date
        else None,
        'target_date': po.target_date.isoformat() if po.target_date else None,
        'binding': event_fact(bindings[-1]) if bindings else None,
        'recorded_email': event_fact(emails[-1]) if emails else None,
        'email_status': 'recorded_only' if emails else 'not_recorded',
        'acknowledgment': event_fact(ack) if ack else None,
        'acknowledgment_current': (
            ack.metadata.get('order_quantity') == str(po_line.quantity)
            and ack.metadata.get('supplier_part_id') == po_line.part_id
            and ack.metadata.get('supplier_id') == po.supplier_id
            and ack.metadata.get('order_revision') == purchase_order_revision(po)
        )
        if ack
        else None,
    }


def procurement_context(actor, work_order, *, stock_visible):
    """Return the additive r8.1 contract; permissions apply before any joins."""
    from django.conf import settings

    from order.models import PurchaseOrderLineItem

    visible = can_read_procurement(actor, work_order)
    kit = None
    if getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False):
        kit = JobKit.objects.filter(work_order=work_order).first()
    body = {
        'schema_version': SCHEMA_VERSION,
        'work_order_id': work_order.pk,
        'work_lifecycle_version': work_order.lifecycle_version,
        'kit_version': kit.version if kit else None,
        'purchasing_visible': visible,
        'stock_visible': stock_visible,
        'approval_policy': 'named_approver_per_po',
        'email_policy': 'recorded_only',
        'lines': [],
        'pending_operations': [],
    }
    if kit is None or not visible:
        return body
    events = list(
        work_order.events
        .filter(event_type__in=PROCUREMENT_EVENT_TYPES)
        .select_related('actor')
        .order_by('pk')
    )
    # Moving shared Work never transfers its original machine's evidence.
    events = [
        event
        for event in events
        if event_matches_context(event, work_order)
        and event.metadata.get('schema_version') == SCHEMA_VERSION
    ]
    configuration = None
    for line in (
        kit.lines
        .select_related('requested_part', 'selected_part')
        .prefetch_related(
            'shortages__purchase_order_line__part__part',
            'shortages__purchase_order_line__order__lines',
            'shortages__purchase_order_line__order__extra_lines',
            'allocations',
        )
        .order_by('sequence')
    ):
        line.kit = kit
        if configuration is None:
            configuration = configuration_revision(work_order, line)
        line_events = [
            event for event in events if event.metadata.get('kit_line_id') == line.pk
        ]
        selections = [
            event for event in line_events if event.event_type == SOURCE_SELECTED
        ]
        selected = selections[-1] if selections else None
        current = (
            selection_is_current(
                selected, work_order, line, configuration=configuration
            )
            if selected
            else None
        )
        orders = {}
        for shortage in line.shortages.all():
            if shortage.purchase_order_line_id:
                po_line = shortage.purchase_order_line
                orders[po_line.pk] = _order(
                    po_line, line_events, selected if current else None
                )
        # A resolved/deleted shortage must not erase durable procurement history.
        linked_ids = {
            event.metadata.get('purchase_order_line_id')
            for event in line_events
            if event.event_type in {PO_LINKED, RECEIPT_RECORDED, PO_ACKNOWLEDGED}
        } - set(orders)
        for po_line in (
            PurchaseOrderLineItem.objects
            .filter(pk__in=linked_ids)
            .select_related('order', 'part__part')
            .prefetch_related('order__lines', 'order__extra_lines')
        ):
            if any(
                event.metadata.get('purchase_order_line_id') == po_line.pk
                and event.metadata.get('purchase_order_id') == po_line.order_id
                for event in line_events
            ):
                orders[po_line.pk] = _order(
                    po_line, line_events, selected if current else None
                )
        receipts = [
            event_fact(event)
            for event in line_events
            if event.event_type == RECEIPT_RECORDED
            and event.metadata.get('purchase_order_line_id') in orders
            and event.metadata.get('purchase_order_id')
            == orders[event.metadata['purchase_order_line_id']]['purchase_order_id']
        ]
        if not stock_visible:
            receipts = []
        buckets = dict.fromkeys(('reserved', 'issued', 'used'), Decimal('0'))
        for allocation in line.allocations.all():
            if allocation.status in {'reserved', 'staged'}:
                buckets['reserved'] += allocation.quantity
            elif allocation.status == 'issued':
                buckets['issued'] += allocation.quantity
            elif allocation.status == 'consumed':
                buckets['used'] += allocation.quantity
        body['lines'].append({
            'kit_line_id': line.pk,
            'key': str(line.key),
            'part_id': line.selected_part_id,
            'required_quantity': str(line.required_quantity),
            'base_uom': line.selected_part.units,
            'requirement_version': line.version,
            'requirement_revision': requirement_revision(work_order, line),
            'configuration_revision': configuration,
            'work_due_date': work_order.due_date.isoformat()
            if work_order.due_date
            else None,
            'needed_at': selected.metadata.get('needed_at') if current else None,
            'dependent_step': selected.metadata.get('dependent_step')
            if current
            else None,
            'source_selection': event_fact(selected) if selected else None,
            'selection_current': current,
            'reserved_quantity': str(buckets['reserved']),
            'issued_quantity': str(buckets['issued']),
            'used_quantity': str(buckets['used']),
            'outstanding_quantity': str(
                max(Decimal('0'), line.required_quantity - sum(buckets.values()))
            ),
            'orders': list(orders.values()),
            'receipts': receipts,
        })
    body['pending_operations'] = (
        list(
            WorkOrderCommand.objects.filter(
                work_order=work_order,
                command__startswith='procurement_',
                status__in=['pending', 'uncertain'],
            ).values('command', 'idempotency_key', 'status', 'result_ref')
        )
        if stock_visible
        else []
    )
    return body
