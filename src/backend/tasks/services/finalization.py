"""The capability that lets a repair packet finalize its own work order.

A packet-owned work order normally refuses to start, transition or complete
through the standalone commands: the packet owns that lifecycle
(``PACKET_OWNS_LIFECYCLE``), and reaching around it would finalize a repair with
no closeout, no parts reconciliation and no machine-history row. The one
legitimate exception is ``repair.services`` driving both aggregates inside a
single transaction.

That exception used to be a ``packet_finalization: bool``. Nothing in the
request path could set it - DRF drops fields no serializer declares, and none
declares this one - but that is a property of today's serializers rather than of
the design. A boolean is exactly the kind of thing an HTTP body can say, so the
guarantee was one careless ``fields = '__all__'`` away from being false.

A :class:`PacketFinalization` cannot be spelled in JSON. It also names the packet
it speaks for, so it authorizes finalizing *that* work order and no other: a
token minted for one packet cannot suppress the check on someone else's. Both
properties are structural - they hold regardless of what any future serializer
or view does.

The token suppresses exactly one check, packet ownership. Permission, scope,
version, readiness, safety gates, open children and the required closeout fields
are enforced identically on both paths.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PacketFinalization:
    """Proof that a repair packet's own finalization path drives this command.

    Minted only by ``repair.services``, which holds the packet row locked for
    the duration of the transaction it passes this into.
    """

    packet_id: int


def is_packet_finalization(token, work_order) -> bool:
    """Whether ``token`` authorizes finalizing ``work_order`` from its packet.

    Fails closed on anything that is not a token minted for the packet that
    actually owns this work order - including ``True``, which is what a
    request-borne value would look like.
    """
    if not isinstance(token, PacketFinalization):
        return False

    # A reverse one-to-one raises RelatedObjectDoesNotExist (an AttributeError)
    # when absent, so getattr's default covers the unowned case.
    packet = getattr(work_order, 'repair_packet', None)
    return packet is not None and packet.pk == token.packet_id
