"""Record exact native PO/PDF/email evidence locally, without a sender path.

The parent lock and unique operation key make recording replay safe. A retry
returns the original bytes even after a later commercial edit. A changed input
under the same key is a conflict, never a replacement document.
"""

import base64
import hashlib
import json
import re
from decimal import Decimal
from email import policy
from email.parser import BytesParser

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from ai.core.integrations.email.commands import _mime
from ai.core.pdf.service import get_pdf_service
from order.models import PurchaseOrder, PurchaseOrderDocumentEvidence
from users.permissions import check_user_permission


class EvidenceConflict(APIException):
    """A stable document identity cannot be reused for different content."""

    status_code = 409
    default_detail = 'Operation key already belongs to different recorded evidence.'


def require_access(actor, *, write=False):
    """Rehydrate native purchasing and evidence grants on every invocation."""
    owner = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    action = 'change' if write else 'view'
    if (
        owner is None
        or not owner.has_perm('users.view_email')
        or not (
            owner.has_perm(f'order.{action}_purchaseorder')
            or check_user_permission(owner, PurchaseOrder, action)
        )
    ):
        raise PermissionDenied('Purchasing and email evidence permissions are required')
    return owner


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _address(value):
    if not isinstance(value, str) or any(c in value for c in ('\r', '\n', '\x00')):
        raise ValidationError('A single explicit mailbox address is required')
    validate_email(value)
    return value


def _priced(line, *, goods, currency):
    price = line.purchase_price if goods else line.price
    if (
        price is None
        or str(price.currency) != currency
        or not price.amount.is_finite()
        or price.amount < 0
    ):
        raise ValidationError(
            'Every goods/charge price must be known in the order currency'
        )
    if line.quantity <= 0 or not 0 <= line.discount <= 100:
        raise ValidationError('Invalid quantity or discount in native order')
    total = line.quantity * price.amount * (1 - line.discount / 100)
    data = {
        'pk': line.pk,
        'quantity': str(line.quantity),
        'discount': str(line.discount),
        'unit_price': str(price.amount),
        'total_price': str(total),
        'currency': currency,
        'reference': line.reference,
        'notes': line.notes,
    }
    if goods:
        sp = line.part
        if sp is None or sp.supplier_id != line.order.supplier_id:
            raise ValidationError('Goods must identify a matching native SupplierPart')
        data.update(
            supplier_part_id=sp.pk,
            part_id=sp.part_id,
            part_name=sp.part.name,
            supplier_part_number=sp.SKU,
            description=sp.description,
            unit=sp.packaging
            or (
                (sp.part.units or 'each')
                if sp.pack_quantity_native == 1
                else 'order unit'
            ),
            base_unit=sp.part.units or 'each',
            pack_quantity=str(sp.pack_quantity_native),
        )
    else:
        data['description'] = line.description
    return data


def _context(order, request, actor):
    if order.supplier is None or not order.order_currency:
        raise ValidationError(
            'Native supplier and explicit order currency are required'
        )
    lines = [
        _priced(line, goods=True, currency=order.currency)
        for line in order.lines.select_related('part__part', 'order').order_by('pk')
    ]
    charges = [
        _priced(line, goods=False, currency=order.currency)
        for line in order.extra_lines.order_by('pk')
    ]
    if not lines:
        raise ValidationError('At least one native goods line is required')
    goods_total = sum((Decimal(line['total_price']) for line in lines), Decimal(0))
    charges_total = sum((Decimal(line['total_price']) for line in charges), Decimal(0))
    native_total = order.calculate_total_price(target_currency=order.currency)
    if native_total is None or native_total.amount != goods_total + charges_total:
        raise ValidationError('Native order total does not match goods plus charges')
    return {
        'schema_version': 1,
        'company_name': 'EQUA AIMMS',
        'order_id': order.pk,
        'reference': order.reference,
        'status': order.status,
        'status_label': str(order.status_text),
        'issue_date': order.issue_date.isoformat() if order.issue_date else None,
        'target_date': order.target_date.isoformat() if order.target_date else None,
        'order_updated_at': order.updated_at.isoformat(),
        'supplier_id': order.supplier_id,
        'supplier_name': order.supplier.name,
        'supplier_address': order.supplier.address or '',
        'supplier_contact_email': order.supplier.email or '',
        'supplier_email': order.supplier.email or '',
        'delivery_address': request['ship_to'],
        'buyer_name': request['buyer_name'],
        'buyer_email': request['from_address'],
        'internal_ref': request.get('internal_ref', ''),
        'lines': lines,
        'extra_lines': charges,
        'subtotal': str(goods_total),
        'charges_total': str(charges_total),
        'total_price': str(native_total.amount),
        'currency': order.currency,
        # The native schema has no standalone tax field. Do not invent zero tax.
        'tax': None,
        'shipping_cost': None,
        'provenance_kind': request['provenance_kind'],
        'recorded_at': timezone.now().isoformat(),
        'actor': {'pk': actor.pk, 'username': actor.get_username()},
    }


@transaction.atomic
def record_documents(*, order_id, actor, data):
    """Create or replay one exact specimen; no SMTP, Gmail or provider calls."""
    actor = require_access(actor, write=True)
    if not isinstance(data, dict):
        raise ValidationError('Recorded evidence payload must be an object')
    allowed = {
        'operation_key',
        'to',
        'from_address',
        'ship_to',
        'buyer_name',
        'internal_ref',
        'provenance_kind',
    }
    if set(data) - allowed:
        raise ValidationError('Unsupported recorded evidence fields')
    key = data.get('operation_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
        raise ValidationError(
            'A stable operation_key of at most 128 safe characters is required'
        )
    for field in ('ship_to', 'buyer_name'):
        if (
            not isinstance(data.get(field), str)
            or not data[field].strip()
            or len(data[field]) > 4000
        ):
            raise ValidationError(f'Explicit {field} is required')
    _address(data.get('to'))
    _address(data.get('from_address'))
    if data.get('provenance_kind') not in ('native_record', 'fixture'):
        raise ValidationError('Declare native_record or fixture provenance')
    if 'internal_ref' in data and (
        not isinstance(data['internal_ref'], str) or len(data['internal_ref']) > 1000
    ):
        raise ValidationError('Invalid internal reference')
    request_hash = _hash(
        json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    )
    order = PurchaseOrder.objects.select_for_update().filter(pk=order_id).first()
    if order is None:
        raise NotFound('Purchase order not found')
    existing = PurchaseOrderDocumentEvidence.objects.filter(operation_key=key).first()
    if existing:
        if existing.order_id != order.pk or existing.request_hash != request_hash:
            raise EvidenceConflict()
        return existing, False
    list(order.lines.select_for_update().order_by('pk'))
    list(order.extra_lines.select_for_update().order_by('pk'))
    context = _context(order, data, actor)
    subject = f'Purchase Order: {order.reference}'
    body = (
        f'Dear {order.supplier.name},\n\n'
        f'Please find purchase order {order.reference} attached.\n'
        f'Total goods and charges: {context["currency"]} {context["total_price"]}.\n'
        f'Deliver to: {data["ship_to"]}\n'
        'Please acknowledge the quantities, terms and promised delivery date.\n\n'
        f'{data["buyer_name"]}\nEQUA AIMMS\n'
    )
    context['email'] = {
        'to': data['to'],
        'from_address': data['from_address'],
        'subject': subject,
        'body': body,
    }
    context['generated_date'] = context['recorded_at']
    pdf = get_pdf_service().purchase_order_pdf(context)
    if not isinstance(pdf, bytes) or not pdf.startswith(b'%PDF-'):
        raise ValidationError('Renderer did not return a PDF')
    # Filename uses the operation digest rather than an untrusted order reference.
    filename = f'purchase-order-{order.pk}-{_hash(key.encode())[:12]}.pdf'
    message = _mime(
        {
            'to': data['to'],
            'subject': subject,
            'body': body,
            'attachments': [
                {
                    'filename': filename,
                    'data_bytes': pdf,
                    'mime_type': 'application/pdf',
                }
            ],
        },
        sender=data['from_address'],
        operation_id=key,
    )
    email = message.as_bytes(policy=policy.SMTP)
    context['email']['filename'] = filename
    try:
        with transaction.atomic():
            evidence = PurchaseOrderDocumentEvidence.objects.create(
                operation_key=key,
                order=order,
                actor=actor,
                request_hash=request_hash,
                context=context,
                pdf_bytes=pdf,
                email_bytes=email,
                pdf_sha256=_hash(pdf),
                email_sha256=_hash(email),
            )
    except IntegrityError as exc:
        # A cross-order duplicate key must never leak or overwrite its first result.
        raise EvidenceConflict() from exc
    return evidence, True


def evidence_result(evidence, *, include_bytes=False):
    """Reconcile stored hashes and attachment content with a known local state."""
    pdf, email = bytes(evidence.pdf_bytes), bytes(evidence.email_bytes)
    msg = BytesParser(policy=policy.default).parsebytes(email)
    attachments = list(msg.iter_attachments())
    verified = (
        _hash(pdf) == evidence.pdf_sha256
        and _hash(email) == evidence.email_sha256
        and len(attachments) == 1
        and attachments[0].get_payload(decode=True) == pdf
        and attachments[0].get_filename() == evidence.context['email']['filename']
        and attachments[0].get_content_type() == 'application/pdf'
    )
    result = {
        'pk': evidence.pk,
        'order_id': evidence.order_id,
        'operation_key': evidence.operation_key,
        'state': 'recorded_not_sent',
        'supplier_email_sent': False,
        'attachment_state': 'verified' if verified else 'corrupt',
        'pdf_sha256': evidence.pdf_sha256,
        'email_sha256': evidence.email_sha256,
        'context': evidence.context,
        'recorded_at': evidence.created_at.isoformat(),
    }
    if include_bytes:
        result.update(
            pdf_base64=base64.b64encode(pdf).decode(),
            email_base64=base64.b64encode(email).decode(),
        )
    return result
