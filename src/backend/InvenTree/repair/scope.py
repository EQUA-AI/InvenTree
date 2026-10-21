"""Repair packets require native Work view and their machine's client scope."""

from django.db.models import F, Q

from tasks.scope import (
    ScopeError,
    require_machine_scope,
    require_work_order_scope,
    scoped_machines,
    scoped_work_orders,
    work_order_scope_filter,
)

from .models import RepairPacket


def scoped_repair_packets(actor):
    """Require machine scope as well as linked Work and packet consistency."""
    try:
        work_order_scope_filter(actor)
    except ScopeError:
        return RepairPacket.objects.none()
    return RepairPacket.objects.filter(machine__in=scoped_machines(actor)).filter(
        Q(
            work_order__in=scoped_work_orders(actor),
            machine_id=F('work_order__machine_id'),
        )
        | Q(work_order__isnull=True)
    )


def require_repair_packet_scope(actor, packet):
    """Authorize a proposed linked-work or standalone packet boundary."""
    work_order_scope_filter(actor)
    require_machine_scope(actor, packet.machine)
    if packet.work_order_id is not None:
        require_work_order_scope(actor, packet.work_order)
        if packet.machine_id != packet.work_order.machine_id:
            raise ScopeError('A linked packet must belong to its work machine')
