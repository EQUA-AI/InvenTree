"""Approval purchasing commands reuse the order serializers and state machine.

Only three explicit operations exist. Creating a draft never issues it or emails
a supplier. Reviewed prices are explicit and never replaced by auto-pricing.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction

from company.models import Company, SupplierPart
from order.models import PurchaseOrder
from order.serializers import (
    PurchaseOrderIssueSerializer,
    PurchaseOrderLineItemSerializer,
    PurchaseOrderSerializer,
)
from order.status_codes import PurchaseOrderStatus
from plugin.events import batch_events

OPERATIONS = frozenset((
    'create_purchase_order',
    'add_po_line_item',
    'issue_purchase_order',
))


def _number(value, *, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Explicit finite quantity and price are required') from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError('Quantity must be positive and price non-negative')
    return number


def _supplier(payload):
    if payload.get('operation', 'create_purchase_order') != 'create_purchase_order':
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
    if operation != 'create_purchase_order':
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
        if not order.lines.exists():
            raise ValueError('Review at least one line before issuing an order')
        result['line_items'] = [
            {
                'supplier_part_id': line.part_id,
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
        if order.extra_lines.exists():
            raise ValueError('Orders with extra charges require a full screen workflow')
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
            'unit': supplier_part.part.units or 'each',
            'quantity': str(quantity),
            'unit_price': str(price),
            'currency': currency,
        })
    result['line_items'] = normalized
    total = sum(
        (
            Decimal(line['quantity']) * Decimal(line['unit_price'])
            for line in normalized
        ),
        Decimal(0),
    )
    if 'total' in result and _number(result['total']) != total:
        raise ValueError('Total differs from the reviewed quantities and prices')
    result['total'] = str(total)
    return result


def baseline(payload):
    """Capture the mutable order state independently of client-supplied baselines."""
    if payload.get('operation', 'create_purchase_order') == 'create_purchase_order':
        return {}
    order = PurchaseOrder.objects.get(pk=payload['order_id'])
    return {
        'order_id': order.pk,
        'updated_at': order.updated_at.isoformat(),
        'status': order.status,
        'supplier_id': order.supplier_id,
        'currency': order.order_currency,
        'lines': [
            [
                line.pk,
                line.part_id,
                str(line.quantity),
                str(line.purchase_price),
                line.destination_id,
            ]
            for line in order.lines.order_by('pk')
        ],
        'extra_lines': list(
            order.extra_lines.order_by('pk').values_list('pk', flat=True)
        ),
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
    if operation != 'create_purchase_order':
        order = PurchaseOrder.objects.select_for_update().get(pk=payload['order_id'])
        # Lock parent and children, then recheck drift inside the effect txn.
        list(order.lines.select_for_update().order_by('pk'))
        if baseline(payload) != approval.baseline_context:
            raise ValueError('Order changed since review; no action completed')
    errors = validate(payload)
    if errors:
        raise ValueError('; '.join(errors))
    if (
        payload['line_items']
        and operation == 'create_purchase_order'
        and not owner.has_perm('order.add_purchaseorderlineitem')
    ):
        raise PermissionError('Purchase order line permission is required')
    # Defer plugin event processing until the effect and its receipt commit.
    with batch_events():
        if operation == 'create_purchase_order':
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
        if operation == 'issue_purchase_order':
            serializer = PurchaseOrderIssueSerializer(data={}, context={'order': order})
            serializer.is_valid(raise_exception=True)
            serializer.save()
        else:
            for line in payload['line_items']:
                data = {
                    'order': order.pk,
                    'part': line['supplier_part_id'],
                    'quantity': line['quantity'],
                    'purchase_price': line['unit_price'],
                    'purchase_price_currency': payload['currency'],
                    'auto_pricing': False,
                    'merge_items': False,
                }
                for key in ('reference', 'notes', 'destination', 'target_date'):
                    if key in line:
                        data[key] = line[key]
                serializer = PurchaseOrderLineItemSerializer(data=data)
                serializer.is_valid(raise_exception=True)
                serializer.save()
    order.refresh_from_db()
    return {
        'order_id': order.pk,
        'reference': order.reference,
        'status': order.status,
        'operation': operation,
        'line_ids': list(order.lines.order_by('pk').values_list('pk', flat=True)),
        'email_sent': False,
    }
