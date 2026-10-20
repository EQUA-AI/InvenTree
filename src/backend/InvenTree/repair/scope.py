"""Repair packet access follows its work owner, or its unlinked asset."""

from django.db.models import F, Q

from tasks.scope import (
    ScopeError,
    require_machine_scope,
    require_work_order_scope,
    scoped_machines,
    scoped_work_orders,
)

from .models import RepairPacket


def scoped_repair_packets(actor):
    """Filter linked work first; a machine must never widen its customer scope."""
    return RepairPacket.objects.filter(
        Q(
            work_order__in=scoped_work_orders(actor),
            machine_id=F('work_order__machine_id'),
        )
        | Q(work_order__isnull=True, machine__in=scoped_machines(actor))
    )


def require_repair_packet_scope(actor, packet):
    """Authorize a proposed linked-work or standalone packet boundary."""
    if packet.work_order_id is not None:
        require_work_order_scope(actor, packet.work_order)
        if packet.machine_id != packet.work_order.machine_id:
            raise ScopeError('A linked packet must belong to its work machine')
    else:
        require_machine_scope(actor, packet.machine)
