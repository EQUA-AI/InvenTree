"""Approval purchasing commands reuse the order serializers and state machine.

Explicit reviewed operations reuse the durable approval execution ledger.
Creating a draft never issues it or emails a supplier. Reviewed prices are explicit and never replaced by auto-pricing.
"""

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from company.models import Company, SupplierPart
from order.models import PurchaseOrder
from order.serializers import (
    PurchaseOrderExtraLineSerializer,
    PurchaseOrderIssueSerializer,
    PurchaseOrderLineItemSerializer,
    PurchaseOrderSerializer,
)
from order.status_codes import PurchaseOrderStatus
from part.models import Part
from plugin.events import batch_events

OPERATIONS = frozenset((
    'create_purchase_order',
    'add_po_line_item',
    'issue_purchase_order',
    'place_purchase_order',
))
CREATE_OPERATIONS = frozenset(('create_purchase_order', 'place_purchase_order'))


def notifications_possible(order):
    """A responsible owner or a part subscriber can trigger normal order mail."""
    return order.responsible_id is not None or any(
        user.pk != order.created_by_id for user in order.subscribed_users()
    )


def _number(value, *, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Explicit finite quantity and price are required') from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError('Quantity must be positive and price non-negative')
    return number


def _supplier(payload):
    if payload.get('operation', 'create_purchase_order') not in CREATE_OPERATIONS:
        order = PurchaseOrder.objects.select_related('supplier').get(
            pk=payload.get('order_id')
        )
        if payload.get('supplier_id') not in (None, order.supplier_id):
            raise ValueError('Supplier differs from the reviewed order')
        supplier = order.supplier
    else:
        supplier = Company.objects.get(pk=payload.get('supplier_id'))
    if supplier is None or not supplier.active or not supplier.is_supplier:
        raise ValueError('An active supplier is required')
    return supplier


def prepare_payload(payload):
    """Resolve unambiguous supplier parts, units, names and exact reviewed totals."""
    if not isinstance(payload, dict):
        raise ValueError('Purchasing payload must be an object')
    result = deepcopy(payload)
    operation = result.setdefault('operation', 'create_purchase_order')
    if operation not in OPERATIONS:
        raise ValueError('Unsupported purchasing operation')
    supplier = _supplier(result)
    result.update(supplier_id=supplier.pk, supplier_name=supplier.name)
    currency = result.get('currency')
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or currency != currency.upper()
    ):
        raise ValueError('An explicit three-letter currency is required')
    if operation not in CREATE_OPERATIONS:
        order = PurchaseOrder.objects.get(pk=result['order_id'])
        if order.status not in (
            PurchaseOrderStatus.PENDING.value,
            PurchaseOrderStatus.ON_HOLD.value,
        ):
            raise ValueError('Only a draft or on-hold order can be changed or issued')
        if currency != order.order_currency:
            raise ValueError('Currency differs from the reviewed order')
        result['order_reference'] = order.reference
    if operation == 'issue_purchase_order':
        order = PurchaseOrder.objects.get(pk=result['order_id'])
        result['notifications_possible'] = notifications_possible(order)
        if not order.lines.exists():
            raise ValueError('Review at least one line before issuing an order')
        result['line_items'] = [
            {
                'pk': line.pk,
                'supplier_part_id': line.part_id,
                'discount': str(line.discount),
                'reference': line.reference,
                'notes': line.notes,
                'destination': line.destination_id,
                'target_date': line.target_date.isoformat()
                if line.target_date
                else None,
                'quantity': str(line.quantity),
                'unit_price': str(line.purchase_price.amount)
                if line.purchase_price is not None
                else None,
                'currency': str(line.purchase_price.currency)
                if line.purchase_price is not None
                else currency,
            }
            for line in order.lines.order_by('pk')
        ]
        result['extra_lines'] = [
            {
                'pk': line.pk,
                'description': line.description,
                'quantity': str(line.quantity),
                'unit_price': str(line.price.amount)
                if line.price is not None
                else None,
                'currency': str(line.price.currency)
                if line.price is not None
                else currency,
                'discount': str(line.discount),
                'reference': line.reference,
                'notes': line.notes,
            }
            for line in order.extra_lines.order_by('pk')
        ]
    lines = result.get('line_items', [])
    if not isinstance(lines, list) or len(lines) > 100:
        raise ValueError('Line items must be a list of at most 100 reviewed lines')
    if operation == 'add_po_line_item' and len(lines) != 1:
        raise ValueError('Add exactly one reviewed line per action')
    normalized = []
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError('Each line must be an object')
        rows = SupplierPart.objects.select_related('part').filter(
            supplier=supplier, active=True
        )
        if line.get('supplier_part_id'):
            rows = rows.filter(pk=line['supplier_part_id'])
        else:
            rows = rows.filter(part_id=line.get('part_id'))
        candidates = list(rows[:2])
        if len(candidates) != 1:
            raise ValueError('Name one unambiguous active supplier part for each line')
        supplier_part = candidates[0]
        if line.get('part_id') not in (None, supplier_part.part_id):
            raise ValueError('Supplier part and internal part do not match')
        if not supplier_part.part.active or not supplier_part.part.purchaseable:
            raise ValueError('Only active purchasable parts can be ordered')
        quantity = _number(line.get('quantity'), positive=True)
        price = _number(line.get('unit_price', line.get('purchase_price')))
        if quantity.as_tuple().exponent < -5 or price.as_tuple().exponent < -6:
            raise ValueError('Quantity or price exceeds supported decimal precision')
        if line.get('currency', currency) != currency:
            raise ValueError('All line prices must use the reviewed order currency')
        normalized.append({
            **line,
            'supplier_part_id': supplier_part.pk,
            'part_id': supplier_part.part_id,
            'part_name': supplier_part.part.name,
            'unit': supplier_part.packaging
            or (
                (supplier_part.part.units or 'each')
                if supplier_part.pack_quantity_native == 1
                else 'order unit'
            ),
            'base_unit': supplier_part.part.units or 'each',
            'pack_quantity': str(supplier_part.pack_quantity_native),
            'supplier_sku': supplier_part.SKU,
            'discount': str(_discount(line.get('discount', '0'))),
            'quantity': str(quantity),
            'unit_price': str(price),
            'currency': currency,
        })
    result['line_items'] = normalized
    charges = result.get('extra_lines', [])
    if not isinstance(charges, list) or len(charges) > 100:
        raise ValueError('Extra lines must be a list of at most 100 reviewed charges')
    if operation == 'add_po_line_item' and charges:
        raise ValueError('Add charges with the reviewed draft or placement operation')
    result['extra_lines'] = []
    for charge in charges:
        if (
            not isinstance(charge, dict)
            or not isinstance(charge.get('description'), str)
            or not charge['description'].strip()
        ):
            raise ValueError('Each charge requires a description')
        quantity = _number(charge.get('quantity'), positive=True)
        price = _number(charge.get('unit_price'))
        if quantity.as_tuple().exponent < -5 or price.as_tuple().exponent < -6:
            raise ValueError('Charge exceeds supported decimal precision')
        if charge.get('currency', currency) != currency:
            raise ValueError('Charges must use the reviewed order currency')
        result['extra_lines'].append({
            **charge,
            'quantity': str(quantity),
            'unit_price': str(price),
            'currency': currency,
            'discount': str(_discount(charge.get('discount', '0'))),
        })
    if operation == 'place_purchase_order' and not normalized:
        raise ValueError('Placement requires at least one reviewed goods line')
    goods = sum((_line_total(line) for line in normalized), Decimal(0))
    charges_total = sum(
        (_line_total(line) for line in result['extra_lines']), Decimal(0)
    )
    total = goods + charges_total
    if 'total' in result and _number(result['total']) != total:
        raise ValueError('Total differs from the reviewed goods and charges')
    result.update(
        goods_total=str(goods), charges_total=str(charges_total), total=str(total)
    )
    return result


def _discount(value):
    discount = _number(value)
    if discount > 100 or discount.as_tuple().exponent < -2:
        raise ValueError('Discount must be between 0 and 100 with at most two decimals')
    return discount


def _line_total(line):
    return (
        Decimal(line['quantity'])
        * Decimal(line['unit_price'])
        * (1 - Decimal(line['discount']) / 100)
    )


def _rows(queryset):
    # values() preserves every commercial field, including currencies, discounts,
    # metadata, references, destinations and charge context, without Money coercion.
    return json.loads(json.dumps(list(queryset.values()), cls=DjangoJSONEncoder))


def baseline(payload):
    """Capture the mutable order state independently of client-supplied baselines."""
    if payload.get('operation', 'create_purchase_order') in CREATE_OPERATIONS:
        return {}
    order = PurchaseOrder.objects.get(pk=payload['order_id'])
    return {
        'order_id': order.pk,
        'updated_at': order.updated_at.isoformat(),
        'status': order.status,
        'supplier_id': order.supplier_id,
        'responsible_id': order.responsible_id,
        'notification_subscriber_ids': sorted(
            user.pk for user in order.subscribed_users()
        ),
        'currency': order.order_currency,
        'header': _rows(PurchaseOrder.objects.filter(pk=order.pk))[0],
        'lines': _rows(order.lines.order_by('pk')),
        'extra_lines': _rows(order.extra_lines.order_by('pk')),
    }


def validate(payload):
    """Stored content must still equal the authoritative purchasing review."""
    try:
        prepared = prepare_payload(payload)
        if prepared != payload:
            return [
                'Purchasing details changed or are incomplete; request a fresh review'
            ]
    except Exception as exc:
        return [str(exc)]
    return []


def _actor(actor, operation):
    owner = get_user_model().objects.get(pk=actor.pk, is_active=True)
    permissions = {
        'create_purchase_order': ('order.add_purchaseorder',),
        'place_purchase_order': (
            'order.add_purchaseorder',
            'order.change_purchaseorder',
            'order.add_purchaseorderlineitem',
        ),
        'add_po_line_item': (
            'order.change_purchaseorder',
            'order.add_purchaseorderlineitem',
        ),
        'issue_purchase_order': ('order.change_purchaseorder',),
    }[operation]
    if not all(owner.has_perm(permission) for permission in permissions):
        raise PermissionError('Purchasing permission is required')
    return owner


@transaction.atomic
def execute(approval, *, actor):
    """Run canonical DB writes inside the dispatch-and-receipt transaction."""
    payload = approval.payload
    operation = payload['operation']
    owner = _actor(actor, operation)
    if operation == 'place_purchase_order' and approval.assigned_to_user_id != owner.pk:
        raise PermissionError('Placement requires the named assigned approver')
    if (
        approval.status != 'executing'
        or not approval.executions.filter(actor=owner, state='submitting').exists()
    ):
        raise PermissionError('Durable authenticated approval execution is required')
    if operation not in CREATE_OPERATIONS:
        order = PurchaseOrder.objects.select_for_update().get(pk=payload['order_id'])
        # Lock parent and children, then recheck drift inside the effect txn.
        list(order.lines.select_for_update().order_by('pk'))
        list(order.extra_lines.select_for_update().order_by('pk'))
        if baseline(payload) != approval.baseline_context:
            raise ValueError('Order changed since review; no action completed')
    Company.objects.select_for_update().get(pk=payload['supplier_id'])
    list(
        SupplierPart.objects
        .select_for_update()
        .filter(pk__in=[line['supplier_part_id'] for line in payload['line_items']])
        .order_by('pk')
    )
    list(
        Part.objects
        .select_for_update()
        .filter(pk__in=[line['part_id'] for line in payload['line_items']])
        .order_by('pk')
    )
    errors = validate(payload)
    if errors:
        raise ValueError('; '.join(errors))
    if (
        payload['line_items']
        and operation in CREATE_OPERATIONS
        and not owner.has_perm('order.add_purchaseorderlineitem')
    ):
        raise PermissionError('Purchase order line permission is required')
    if (
        payload['extra_lines']
        and operation in CREATE_OPERATIONS
        and not owner.has_perm('order.add_purchaseorderextraline')
    ):
        raise PermissionError('Purchase order charge permission is required')
    # Defer plugin event processing until the effect and its receipt commit.
    with batch_events():
        if operation in CREATE_OPERATIONS:
            data = {
                'supplier': payload['supplier_id'],
                'order_currency': payload['currency'],
                'description': payload.get('description', ''),
            }
            for key in (
                'reference',
                'target_date',
                'link',
                'contact',
                'responsible',
                'destination',
            ):
                if key in payload:
                    data[key] = payload[key]
            serializer = PurchaseOrderSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            order = serializer.save(created_by=owner)
        if operation != 'issue_purchase_order':
            for line in payload['line_items']:
                data = {
                    'order': order.pk,
                    'part': line['supplier_part_id'],
                    'quantity': line['quantity'],
                    'purchase_price': line['unit_price'],
                    'purchase_price_currency': payload['currency'],
                    'discount': line['discount'],
                    'auto_pricing': False,
                    'merge_items': False,
                }
                for key in ('reference', 'notes', 'destination', 'target_date'):
                    if key in line:
                        data[key] = line[key]
                serializer = PurchaseOrderLineItemSerializer(data=data)
                serializer.is_valid(raise_exception=True)
                serializer.save()
            for charge in payload['extra_lines']:
                data = {
                    'order': order.pk,
                    'description': charge['description'],
                    'quantity': charge['quantity'],
                    'price': charge['unit_price'],
                    'price_currency': payload['currency'],
                    'discount': charge['discount'],
                }
                for key in ('reference', 'notes', 'target_date', 'link'):
                    if key in charge:
                        data[key] = charge[key]
                serializer = PurchaseOrderExtraLineSerializer(data=data)
                serializer.is_valid(raise_exception=True)
                serializer.save()
        if operation in ('issue_purchase_order', 'place_purchase_order'):
            serializer = PurchaseOrderIssueSerializer(
                data={},
                context={'order': order, 'notify': operation != 'place_purchase_order'},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
    order.refresh_from_db()
    if operation in CREATE_OPERATIONS or operation == 'issue_purchase_order':
        if order.total_price is None or order.total_price.amount != Decimal(
            payload['total']
        ):
            raise ValueError('Persisted native total differs from the reviewed total')
    return {
        'order_id': order.pk,
        'reference': order.reference,
        'status': order.status,
        'operation': operation,
        'line_ids': list(order.lines.order_by('pk').values_list('pk', flat=True)),
        'charge_ids': list(
            order.extra_lines.order_by('pk').values_list('pk', flat=True)
        ),
        'total': str(order.total_price.amount)
        if order.total_price is not None
        else None,
        'currency': order.currency,
        'approved_by_id': owner.pk,
        'approved_by': owner.get_username(),
        'supplier_email_sent': False,
        'email_sent': None if payload.get('notifications_possible') else False,
        'internal_notifications_possible': bool(payload.get('notifications_possible')),
    }
