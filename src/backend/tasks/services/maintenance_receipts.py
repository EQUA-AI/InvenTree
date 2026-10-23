"""One-line native maintenance receiving, durable replay and committed continuation."""

import hashlib
import json

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from rest_framework.exceptions import APIException, ValidationError

from order.models import PurchaseOrder, PurchaseOrderLineItem
from stock.models import StockItem
from stock.serializers import StockItemSerializer
from tasks.jobkit_models import JobKitLine, JobKitShortage
from tasks.materials_readout import can_read_work_stock
from tasks.models import (
    FulfillmentMode,
    MaintenanceReceipt,
    MaintenanceReceiptStock,
    WorkOrderCommand,
)
from tasks.permissions import RESERVE_JOBKIT, require_permission
from tasks.services.job_kits import (
    _derive_kit_status,
    _fulfilled_for_line,
    _locked_kit,
    _reconcile_line_shortage,
    _require_reservable,
)
from tasks.services.procedure_execution import _record_command
from tasks.services.procurement import (
    RECEIPT_ALLOCATED,
    RECEIPT_RECORDED,
    scope_snapshot,
)
from tasks.services.stock_allocation import StockOverAllocation, mutate_stock_allocation
from tasks.services.work_orders import (
    WorkOrderScopeError,
    _canonical_hash,
    _locked_work_order,
    _require_scope,
)
from users.permissions import check_user_role


class ReceiptConflict(APIException):
    """The stable receipt intent disagrees with current or recorded facts."""

    status_code = 409
    default_code = 'MAINTENANCE_RECEIPT_CONFLICT'
    code = default_code


def _authorize(actor, work_order):
    try:
        _require_scope(actor, work_order)
    except WorkOrderScopeError as exc:
        raise PermissionDenied('Work scope is not authorized') from exc
    if not can_read_work_stock(actor, work_order):
        raise PermissionDenied('Stock access outside the Work machine scope')
    if not check_user_role(actor, 'purchase_order', 'change') or not check_user_role(
        actor, 'stock', 'add'
    ):
        raise PermissionDenied('Purchasing change and stock add permissions required')


def _requirement(work_order, kit, line):
    """Capture requirement and machine binding, independent of mutable custody."""
    return {
        'line': line.pk,
        'version': line.version,
        'part': line.selected_part_id,
        'quantity': str(line.required_quantity),
        'required': line.required,
        'fulfillment_mode': line.fulfillment_mode,
        'source_application_hash': kit.source_application_hash,
        'machine': work_order.machine_id,
        'customer': work_order.customer_id,
        'scope': str(work_order.machine.client_id) if work_order.machine_id else None,
    }


def _require_current_application(work_order, kit):
    if work_order.lifecycle_status in ('completed', 'canceled'):
        raise ReceiptConflict('Maintenance Work is terminal')
    primary = work_order.procedure_applications.filter(primary=True).first()
    if kit.source_application_hash and (
        primary is None or primary.snapshot_hash != kit.source_application_hash
    ):
        raise ReceiptConflict('The Job Kit procedure requirement has changed')


def _snapshot(items):
    if isinstance(items, list):
        items = StockItem.objects.filter(pk__in=[item.pk for item in items]).order_by(
            'pk'
        )
    queryset = StockItemSerializer.annotate_queryset(items)
    return json.loads(
        json.dumps(StockItemSerializer(queryset, many=True).data, default=str)
    )


def receive_purchase_order(*, request, order_id, serializer_factory):
    """Wrap the native action; authorize and replay before native validation.

    Receipt/association/command commit together. on_commit runs separately, so
    physical receipt cannot be undone by a reservation failure. An exact native
    request replay also retries an interrupted continuation without receiving.
    """
    data = request.data
    operation_key = data.get('maintenance_operation_key')
    if operation_key is not None and (
        not isinstance(operation_key, str)
        or not operation_key.strip()
        or len(operation_key) > 128
    ):
        raise ValidationError({
            'maintenance_operation_key': 'A nonempty key of at most 128 characters is required'
        })
    request_hash = _canonical_hash(
        'maintenance_receive', request.user, {'order_id': int(order_id), 'body': data}
    )
    prior = (
        MaintenanceReceipt.objects.filter(operation_key=operation_key).first()
        if operation_key
        else None
    )
    linked = list(
        JobKitShortage.objects
        .filter(purchase_order_line__order_id=order_id)
        .values_list('line__kit__work_order_id', flat=True)
        .distinct()
    )
    if prior:
        linked = [prior.work_order_id]
    if not linked and not operation_key:
        # Unrelated native receipts retain their native behavior.
        serializer = serializer_factory(data=data)
        serializer.is_valid(raise_exception=True)
        return _snapshot(serializer.save())
    if not getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False):
        raise Http404
    if len(linked) != 1:
        raise ReceiptConflict(
            'Receipt requires one unambiguous maintenance Work binding'
        )
    with transaction.atomic():
        work_order = _locked_work_order(linked[0])
        _authorize(request.user, work_order)
        kit = _locked_kit(work_order.pk)
        # Re-read under the aggregate lock: simultaneous requests converge here.
        prior = (
            MaintenanceReceipt.objects.filter(operation_key=operation_key).first()
            if operation_key
            else None
        )
        if prior:
            if (
                prior.request_hash != request_hash
                or prior.purchase_order_line.order_id != int(order_id)
            ):
                raise ReceiptConflict(
                    'Operation key was reused with a different receipt'
                )
            transaction.on_commit(lambda: continue_receipt(prior.pk, request.user))
            return prior.response_snapshot
        if not operation_key:
            raise ValidationError({
                'maintenance_operation_key': 'Required for maintenance-linked receiving'
            })
        if MaintenanceReceipt.objects.filter(
            work_order=work_order, reservation_state='pending'
        ).exists():
            raise ReceiptConflict(
                'Replay the pending receipt operation before a new receipt'
            )
        if WorkOrderCommand.objects.filter(
            work_order=work_order, idempotency_key=operation_key
        ).exists():
            raise ReceiptConflict('Operation key belongs to another Work command')
        _require_reservable(kit)
        _require_current_application(work_order, kit)
        order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
        serializer = serializer_factory(data=data)
        # Ensure native validation/save uses the very order we locked.
        serializer.context['order'] = order
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data['items']
        if len(items) != 1 or order.lines.count() != 1:
            raise ValidationError('Maintenance receiving requires one goods line')
        item = items[0]
        po_line = PurchaseOrderLineItem.objects.select_for_update().get(
            pk=item['line_item'].pk
        )
        if po_line.order_id != order.pk:
            raise ValidationError('Receipt line does not belong to this PO')
        shortages = list(
            JobKitShortage.objects.select_for_update().filter(
                purchase_order_line=po_line,
                line__kit=kit,
                status__in=['open', 'requested', 'ordered', 'partial'],
            )
        )
        if len(shortages) != 1:
            raise ReceiptConflict(
                'Receipt requires exactly one current linked shortage'
            )
        shortage = shortages[0]
        line = JobKitLine.objects.select_for_update().get(pk=shortage.line_id)
        version = serializer.validated_data.get('maintenance_requirement_version')
        if version is None or version != line.version:
            raise ReceiptConflict(
                'Supply the current JobKitLine.version as maintenance_requirement_version'
            )
        if (
            not line.required
            or line.fulfillment_mode != FulfillmentMode.RESERVE_CONSUME
            or not po_line.part_id
            or po_line.part.part_id != line.selected_part_id
            or po_line.part.supplier_id != order.supplier_id
            or not po_line.part.part.active
            or po_line.part.part.virtual
            or po_line.part.part.trackable
            or item.get('serial_numbers')
            or po_line.build_order_id
        ):
            raise ReceiptConflict(
                'Receipt does not match the current nonserialized maintenance requirement'
            )
        if not item.get('batch_code', '').strip():
            raise ValidationError({'batch_code': 'Batch traceability is required'})
        location = item['location']
        if (
            location.structural
            or location.external
            or not location.check_ownership(request.user)
        ):
            raise PermissionDenied(
                'Receipt location is not permitted for maintenance stock'
            )
        if item['quantity'] > po_line.quantity - po_line.received:
            raise ReceiptConflict(
                'Receipt quantity exceeds the remaining PO line quantity'
            )
        before = po_line.received
        stocks = list(serializer.save())
        po_line.refresh_from_db(fields=['received'])
        response = _snapshot(stocks)
        facts = [
            {
                'stock_item': stock.pk,
                'quantity': str(stock.quantity),
                'batch': stock.batch,
                'location': stock.location_id,
                'status': stock.status,
                'status_custom_key': stock.status_custom_key,
                'expiry_date': stock.expiry_date.isoformat()
                if stock.expiry_date
                else None,
            }
            for stock in stocks
        ]
        event = _record_command(
            work_order=work_order,
            actor=request.user,
            command='maintenance_receive',
            event_type=RECEIPT_RECORDED,
            idempotency_key=operation_key,
            request_hash=request_hash,
            metadata={
                **scope_snapshot(work_order, line.pk),
                'purchase_order_id': po_line.order_id,
                'purchase_order_line_id': po_line.pk,
                'operation_key': operation_key,
                'po_line': po_line.pk,
                'kit_line': line.pk,
                'shortage': shortage.pk,
                'received_before': str(before),
                'received_after': str(po_line.received),
                'stocks': facts,
            },
        )
        receipt = MaintenanceReceipt.objects.create(
            operation_key=operation_key,
            request_hash=request_hash,
            request_snapshot=json.loads(json.dumps(data, default=str)),
            work_order=work_order,
            kit_line=line,
            shortage=shortage,
            purchase_order_line=po_line,
            event=event,
            actor=request.user,
            requirement_snapshot=_requirement(work_order, kit, line),
            received_before=before,
            received_after=po_line.received,
            response_snapshot=response,
        )
        for stock, fact in zip(stocks, facts, strict=True):
            MaintenanceReceiptStock.objects.create(
                receipt=receipt, stock_item=stock, snapshot=fact
            )
        transaction.on_commit(lambda: continue_receipt(receipt.pk, request.user))
        return response


def continue_receipt(receipt_id, actor):
    """Retry only allocation; preserve physical receipt even on unexpected failure."""
    try:
        return _continue_receipt(receipt_id, actor)
    except Exception as exc:
        # The allocation transaction rolled back; keep a durable recoverable
        # attention state. Never expose exception text or roll back receipt.
        MaintenanceReceipt.objects.filter(pk=receipt_id).exclude(
            reservation_state='allocated'
        ).update(
            reservation_state='attention',
            reservation_code=getattr(exc, 'code', 'RESERVATION_FAILED'),
        )
        return None


@transaction.atomic
def _continue_receipt(receipt_id, actor):
    receipt = MaintenanceReceipt.objects.get(pk=receipt_id)
    work_order = _locked_work_order(receipt.work_order_id)
    _authorize(actor, work_order)
    require_permission(actor, RESERVE_JOBKIT)
    kit = _locked_kit(work_order.pk)
    receipt = MaintenanceReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.reservation_state == 'allocated':
        return receipt
    _require_reservable(kit)
    _require_current_application(work_order, kit)
    line = JobKitLine.objects.select_for_update().get(pk=receipt.kit_line_id)
    if _requirement(work_order, kit, line) != receipt.requirement_snapshot:
        raise ReceiptConflict(
            'Requirement changed after receipt; reconcile the current Work scope'
        )
    needed = max(line.required_quantity - _fulfilled_for_line(line), 0)
    stock_ids = list(receipt.stocks.values_list('stock_item_id', flat=True))
    for stock in StockItem.objects.filter(pk__in=stock_ids).order_by('pk'):
        if needed <= 0:
            break
        take = min(needed, stock.unallocated_quantity())
        if take <= 0:
            continue
        # A further increment after partial fulfillment is a distinct intent;
        # each exact intent is durable under the shared allocator's ledger.
        intent = f'receipt:{receipt.pk}:{stock.pk}:{_fulfilled_for_line(line)}'
        try:
            mutate_stock_allocation(
                stock_item_id=stock.pk,
                line_id=line.pk,
                requested_quantity=take,
                actor=actor,
                idempotency_key=hashlib.sha256(intent.encode()).hexdigest(),
            )
        except StockOverAllocation:
            continue
        needed = max(line.required_quantity - _fulfilled_for_line(line), 0)
    allocations = line.allocations.filter(
        stock_item_id__in=stock_ids,
        status__in=['reserved', 'staged', 'issued', 'consumed'],
    )
    matched = allocations.exists()
    receipt.allocation_ids = list(allocations.values_list('pk', flat=True))
    receipt.reservation_state = 'allocated' if needed <= 0 and matched else 'attention'
    receipt.reservation_code = (
        '' if receipt.reservation_state == 'allocated' else 'RECEIPT_STOCK_NOT_RESERVED'
    )
    receipt.continued_at = timezone.now()
    receipt.save(
        update_fields=[
            'allocation_ids',
            'reservation_state',
            'reservation_code',
            'continued_at',
        ]
    )
    if matched:
        shortage = JobKitShortage.objects.select_for_update().get(
            pk=receipt.shortage_id
        )
        if shortage.purchase_order_line_id != receipt.purchase_order_line_id:
            raise ReceiptConflict('Shortage PO binding changed after receipt')
        shortage.status = 'received' if needed <= 0 else 'partial'
        shortage.resolved_at = timezone.now() if needed <= 0 else None
        shortage.save(update_fields=['status', 'resolved_at'])
    _reconcile_line_shortage(line, needed)
    kit.status = _derive_kit_status(kit)
    kit.version += 1
    kit.save(update_fields=['status', 'version', 'updated_at'])
    if receipt.reservation_state == 'allocated':
        _record_command(
            work_order=work_order,
            actor=actor,
            command='continue_maintenance_receipt',
            event_type=RECEIPT_ALLOCATED,
            idempotency_key=f'receipt:{receipt.pk}:allocated',
            request_hash=receipt.request_hash,
            metadata={
                **scope_snapshot(work_order, line.pk),
                'purchase_order_id': receipt.purchase_order_line.order_id,
                'purchase_order_line_id': receipt.purchase_order_line_id,
                'receipt_id': receipt.pk,
                'operation_key': receipt.operation_key,
                'po_line': receipt.purchase_order_line_id,
                'kit_line': line.pk,
                'allocation_ids': receipt.allocation_ids,
                'stock_ids': stock_ids,
            },
        )
    return receipt
