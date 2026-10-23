"""Job Kit custody lifecycle services: stage, issue, consume, return.

Consume performs a real InvenTree stock removal through
``StockItem.take_stock`` and records the resulting stock-tracking identifier
(design contract 12.5). It is never a bare status flip. Active states
(reserved/staged/issued) continue to count against availability; consume, return,
and release are terminal and free the reservation without double-counting.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from stock.models import StockItem
from stock.status_codes import StockHistoryCode
from tasks.jobkit_models import (
    ACTIVE_ALLOCATION_STATUSES,
    JobKitAllocation,
    JobKitAllocationStatus,
)
from tasks.permissions import ISSUE_JOBKIT, STAGE_JOBKIT, require_permission
from tasks.services.job_kits import (
    JobKitError,
    JobKitLineError,
    JobKitStateError,
    _locked_kit,
    _require_stock_scope,
)
from tasks.services.work_orders import _locked_work_order, _require_scope

_ACTIVE = [s.value for s in ACTIVE_ALLOCATION_STATUSES]


class JobKitCustodyError(JobKitError):
    """A custody transition could not be applied."""

    code = 'JOB_KIT_CUSTODY_ERROR'


def _locked_allocation(kit, allocation_id):
    allocation = (
        JobKitAllocation.objects
        .select_for_update()
        .select_related('line')
        .filter(pk=allocation_id, line__kit=kit)
        .first()
    )
    if allocation is None:
        raise JobKitLineError('Allocation does not belong to this work order')
    return allocation


def _require_source_state(allocation, allowed):
    if allocation.status not in allowed:
        raise JobKitStateError(
            f'Allocation in state {allocation.status!r} cannot transition here'
        )


def _resolve(work_order_id, allocation_id, actor, permission, *, readback=False):
    """Shared lock/authorize preamble for a custody transition."""
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, permission)
    _require_scope(actor, work_order)
    _require_stock_scope(actor, work_order)
    if not readback and work_order.lifecycle_status in ('completed', 'canceled'):
        raise JobKitStateError('Terminal Work custody is immutable')
    kit = _locked_kit(work_order_id)
    allocation = _locked_allocation(kit, allocation_id)
    # Procurement allocations retain receipt-time machine/client identity.
    # Moving shared Work must not transfer custody authority for old receipts.
    from tasks.receipt_models import MaintenanceReceipt
    from tasks.services.procurement import event_matches_context

    receipts = MaintenanceReceipt.objects.filter(
        kit_line_id=allocation.line_id, stocks__stock_item_id=allocation.stock_item_id
    ).select_related('event')
    if any(
        not event_matches_context(receipt.event, work_order) for receipt in receipts
    ):
        from tasks.services.work_orders import WorkOrderScopeError

        raise WorkOrderScopeError('Receipt scope changed before custody action')
    return work_order, kit, allocation


@transaction.atomic
def stage_allocation(
    *, work_order_id, allocation_id, actor, scan_proof=None, correlation_id=None
):
    """Physically confirm a reserved item is staged for the job."""
    _work_order, kit, allocation = _resolve(
        work_order_id, allocation_id, actor, STAGE_JOBKIT
    )
    _require_source_state(allocation, [JobKitAllocationStatus.RESERVED])
    if allocation.line.requires_scan and not scan_proof:
        raise JobKitCustodyError('Scan proof is required to stage this line')
    allocation.status = JobKitAllocationStatus.STAGED
    allocation.staged_by = actor
    allocation.staged_at = timezone.now()
    if scan_proof:
        allocation.scan_proof = scan_proof
    allocation.save(update_fields=['status', 'staged_by', 'staged_at', 'scan_proof'])
    _bump_kit(kit)
    return allocation


@transaction.atomic
def issue_allocation(
    *, work_order_id, allocation_id, actor, correlation_id=None, reconcile_only=False
):
    """Record custody leaving the storeroom; the reservation remains active."""
    _work_order, kit, allocation = _resolve(
        work_order_id, allocation_id, actor, ISSUE_JOBKIT, readback=reconcile_only
    )
    if reconcile_only:
        if allocation.issued_at is None or allocation.issued_by_id != actor.pk:
            raise JobKitCustodyError('Original issue is not confirmed for this actor')
        return allocation
    _require_source_state(
        allocation, [JobKitAllocationStatus.RESERVED, JobKitAllocationStatus.STAGED]
    )
    allocation.status = JobKitAllocationStatus.ISSUED
    allocation.issued_at = timezone.now()
    allocation.issued_by = actor
    allocation.save(update_fields=['status', 'issued_at', 'issued_by'])
    _bump_kit(kit)
    return allocation


@transaction.atomic
def consume_allocation(
    *, work_order_id, allocation_id, actor, correlation_id=None, reconcile_only=False
):
    """Perform a real stock removal for a consumed part/consumable.

    Removes the allocated quantity from the exact stock item through the real
    InvenTree primitive (creating a stock-tracking entry) and marks the
    allocation consumed. The physical decrement and the reservation release net
    to zero double-count against availability.
    """
    work_order, kit, allocation = _resolve(
        work_order_id, allocation_id, actor, ISSUE_JOBKIT, readback=reconcile_only
    )
    # Recovery is read-only and explicit. Ordinary duplicate consume retains the
    # native refusal; an uncertain browser retry never repeats a physical use.
    if reconcile_only:
        if (
            allocation.status != JobKitAllocationStatus.CONSUMED
            or allocation.stock_tracking_id is None
        ):
            raise JobKitCustodyError('Original use is not confirmed by stock tracking')
        return allocation
    _require_source_state(allocation, _ACTIVE)

    stock_item = StockItem.objects.select_for_update().get(pk=allocation.stock_item_id)
    if stock_item.serialized:
        raise JobKitCustodyError(
            'Serialized stock consume is not supported in this slice'
        )
    reference = work_order.reference or f'WO-{work_order.pk}'
    removed = stock_item.take_stock(
        Decimal(allocation.quantity),
        actor,
        code=StockHistoryCode.STOCK_REMOVE,
        notes=f'Consumed for maintenance {reference}',
    )
    if not removed:
        raise JobKitCustodyError('Real stock removal failed; nothing consumed')

    tracking = stock_item.tracking_info.order_by('-pk').first()
    allocation.stock_tracking_id = tracking.pk if tracking else None
    allocation.status = JobKitAllocationStatus.CONSUMED
    allocation.disposed_at = timezone.now()
    allocation.save(update_fields=['status', 'disposed_at', 'stock_tracking_id'])
    from tasks.services.closeout_reconcile import sync_pending_custody_usage

    sync_pending_custody_usage(work_order, allocation, actor)
    _bump_kit(kit)
    return allocation


@transaction.atomic
def return_allocation(*, work_order_id, allocation_id, actor, correlation_id=None):
    """Return a reusable tool/safety item; no stock is consumed."""
    work_order, kit, allocation = _resolve(
        work_order_id, allocation_id, actor, ISSUE_JOBKIT
    )
    _require_source_state(allocation, _ACTIVE)
    allocation.status = JobKitAllocationStatus.RETURNED
    allocation.disposed_at = timezone.now()
    allocation.save(update_fields=['status', 'disposed_at'])
    from tasks.services.closeout_reconcile import sync_pending_custody_usage

    sync_pending_custody_usage(work_order, allocation, actor)
    _bump_kit(kit)
    return allocation


def _bump_kit(kit):
    """Advance the kit optimistic token so custody changes are observable."""
    kit.version = kit.version + 1
    kit.save(update_fields=['version', 'updated_at'])
