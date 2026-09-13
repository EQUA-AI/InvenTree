"""Explicit draft sales-order contract; never issue, allocate, ship or contact."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from .purchasing import _number


def require_role(actor, permission='view'):
    """Sales roles are global business permissions, not customer-as-tenant claims."""
    actor = get_user_model().objects.get(pk=actor.pk, is_active=True)
    if (
        not actor.is_superuser
        and not actor.groups.filter(
            rule_sets__name='sales_order', **{f'rule_sets__can_{permission}': True}
        ).exists()
    ):
        raise PermissionError(f'Sales order {permission} permission is required')
    return actor


def prepare_payload(payload, *, actor=None):
    """Reject omitted pricing and unknown fields; no automatic price substitution."""
    from company.models import Company
    from part.models import Part

    if actor is not None:
        require_role(actor)
    allowed = {
        'operation',
        'customer_id',
        'customer_name',
        'line_items',
        'currency',
        'total',
        'description',
        'customer_reference',
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('Unsupported sales-order payload fields')
    if payload.get('operation', 'create_sales_order') != 'create_sales_order':
        raise ValueError('Only draft sales-order creation is supported')
    customer = Company.objects.get(
        pk=payload.get('customer_id'), is_customer=True, active=True
    )
    currency = payload.get('currency')
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
        or currency != currency.upper()
    ):
        raise ValueError('An explicit three-letter currency is required')
    lines = payload.get('line_items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise ValueError('Review between one and 100 sales-order lines')
    normalized = []
    for line in lines:
        if not isinstance(line, dict) or set(line) - {
            'part_id',
            'part_name',
            'IPN',
            'unit',
            'quantity',
            'unit_price',
            'currency',
        }:
            raise ValueError('Unsupported sales-order line fields')
        part = Part.objects.get(pk=line.get('part_id'), active=True, salable=True)
        quantity, price = (
            _number(line.get('quantity'), positive=True),
            _number(line.get('unit_price')),
        )
        if (
            quantity.as_tuple().exponent < -5
            or price.as_tuple().exponent < -6
            or quantity >= Decimal('10000000000')
        ):
            raise ValueError('Quantity or price exceeds supported precision')
        if line.get('currency', currency) != currency:
            raise ValueError('All prices must use the reviewed currency')
        normalized.append({
            'part_id': part.pk,
            'part_name': part.name,
            'IPN': part.IPN or '',
            'unit': part.units or 'each',
            'quantity': str(quantity),
            'unit_price': str(price),
            'currency': currency,
        })
    total = sum(
        (
            Decimal(line['quantity']) * Decimal(line['unit_price'])
            for line in normalized
        ),
        Decimal(0),
    )
    if 'total' in payload and _number(payload['total']) != total:
        raise ValueError('Total does not match reviewed line quantities and prices')
    result = {
        'operation': 'create_sales_order',
        'customer_id': customer.pk,
        'customer_name': customer.name,
        'line_items': normalized,
        'currency': currency,
        'total': str(total),
    }
    for field, limit in (('description', 250), ('customer_reference', 100)):
        value = payload.get(field, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'{field} exceeds its supported length')
        result[field] = value
    return result


def validate(payload):
    """Require the retained review to match freshly resolved identities."""
    try:
        return (
            []
            if prepare_payload(payload) == payload
            else ['Sales-order details changed; request a fresh review']
        )
    except Exception as exc:
        return [str(exc)]


def execute(approval, actor):
    """Existing approval transaction owns effect, unique execution key and receipt."""
    from company.models import Company
    from order.serializers import SalesOrderLineItemSerializer, SalesOrderSerializer
    from part.models import Part
    from plugin.events import batch_events

    actor = require_role(actor, 'add')
    # Django model permissions may be independently revoked from a role.
    if not actor.has_perm('order.add_salesorder') or not actor.has_perm(
        'order.add_salesorderlineitem'
    ):
        raise PermissionError('Sales order and line creation permissions are required')
    payload = approval.payload
    Company.objects.select_for_update().get(pk=payload['customer_id'])
    list(
        Part.objects
        .select_for_update()
        .filter(pk__in=[line['part_id'] for line in payload['line_items']])
        .order_by('pk')
    )
    if prepare_payload(payload) != payload or payload != approval.baseline_context:
        raise ValueError('Sales-order details changed since review')
    with batch_events():
        serializer = SalesOrderSerializer(
            data={
                'customer': payload['customer_id'],
                'order_currency': payload['currency'],
                'description': payload['description'],
                'customer_reference': payload['customer_reference'],
            }
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save(created_by=actor)
        for line in payload['line_items']:
            serializer = SalesOrderLineItemSerializer(
                data={
                    'order': order.pk,
                    'part': line['part_id'],
                    'quantity': line['quantity'],
                    'sale_price': line['unit_price'],
                    'sale_price_currency': payload['currency'],
                    'auto_pricing': False,
                    'merge_items': False,
                }
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
    order.refresh_from_db()
    return {
        'operation': 'create_sales_order',
        'order_id': order.pk,
        'order_reference': order.reference,
        'status': order.status,
        'line_ids': list(order.lines.order_by('pk').values_list('pk', flat=True)),
        'email_sent': False,
        'issued': False,
        'allocated': False,
        'shipped': False,
    }


def visible(approval, actor):
    """Only assigned reviewers with current sales visibility see this contract."""
    try:
        require_role(actor)
        from company.models import Company

        return Company.objects.filter(
            pk=approval.payload.get('customer_id'), is_customer=True
        ).exists()
    except Exception:
        return False
