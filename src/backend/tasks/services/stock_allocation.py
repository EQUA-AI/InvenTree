"""Shared locked stock-allocation authority for maintenance reservations.

``mutate_stock_allocation`` is the single concurrency authority for creating or
extending a Job Kit reservation against a stock item (design contract 12.3). It
holds a ``SELECT ... FOR UPDATE`` row lock on the stock item while it recomputes
committed allocation across all four domains (build + sales + transfer + job
kit), so competing reservations serialize and can never over-allocate. Model
``clean()`` methods remain defensive guardrails but are not the concurrency
authority.
"""

from decimal import Decimal

from django.db import transaction

from stock.models import StockItem
from tasks.jobkit_models import (
    ACTIVE_ALLOCATION_STATUSES,
    JobKitAllocation,
    JobKitAllocationStatus,
)

ALLOCATION_KIND_JOB_KIT = 'job_kit'

_ACTIVE_STATUS_VALUES = [s.value for s in ACTIVE_ALLOCATION_STATUSES]


class StockAllocationError(Exception):
    """Base error for shared stock-allocation mutations."""

    code = 'STOCK_ALLOCATION_ERROR'


class StockOverAllocation(StockAllocationError):  # noqa: N818 - established command error name
    """The requested quantity exceeds unallocated availability under lock."""

    code = 'STOCK_OVER_ALLOCATED'


def _location_snapshot(stock_item):
    """Capture the source location at reservation time for audit and staging."""
    return {
        'location_id': stock_item.location_id,
        'location_name': str(stock_item.location) if stock_item.location_id else None,
    }


@transaction.atomic
def mutate_stock_allocation(
    *,
    stock_item_id,
    line_id,
    requested_quantity,
    actor,
    idempotency_key,
    allocation_kind=ALLOCATION_KIND_JOB_KIT,
):
    """Reserve stock for a Job Kit line without ever over-allocating.

    Locks the stock row, recomputes four-domain committed allocation while the
    row is held, and creates or extends the active reservation for
    ``(line, stock_item)`` up to available. Idempotent on
    ``(line, stock_item, idempotency_key)``. Raises ``StockOverAllocation`` when
    the request cannot be satisfied from unallocated stock.
    """
    if allocation_kind != ALLOCATION_KIND_JOB_KIT:
        raise StockAllocationError(f'Unsupported allocation kind: {allocation_kind!r}')
    requested_quantity = Decimal(requested_quantity)
    if requested_quantity <= 0:
        raise StockAllocationError('Requested quantity must be greater than zero')

    stock_item = StockItem.objects.select_for_update().get(pk=stock_item_id)

    # Idempotent replay: the exact prior command returns its durable row.
    replay = JobKitAllocation.objects.filter(
        line_id=line_id, stock_item=stock_item, idempotency_key=idempotency_key
    ).first()
    if replay is not None:
        return replay

    # Merge into the one active reservation for this (line, stock_item), if any.
    active = (
        JobKitAllocation.objects
        .select_for_update()
        .filter(
            line_id=line_id, stock_item=stock_item, status__in=_ACTIVE_STATUS_VALUES
        )
        .first()
    )

    committed = stock_item.total_committed_allocation(
        exclude_job_kit={'pk': active.pk} if active is not None else None
    )
    available = Decimal(stock_item.quantity) - committed

    if requested_quantity > available:
        raise StockOverAllocation(
            f'Requested {requested_quantity} exceeds available {available} '
            f'for stock item {stock_item_id}'
        )

    if active is not None:
        active.quantity = active.quantity + requested_quantity
        active.save(update_fields=['quantity'])
        return active

    return JobKitAllocation.objects.create(
        line_id=line_id,
        stock_item=stock_item,
        quantity=requested_quantity,
        status=JobKitAllocationStatus.RESERVED,
        reserved_by=actor,
        idempotency_key=idempotency_key,
        source_location_snapshot=_location_snapshot(stock_item),
    )
