"""Physical authority is recorded against the existing packet safety gate."""

from django.conf import settings
from django.db import models

from tasks.authority_models import AuthorityEntry


class GateAuthorityRecord(AuthorityEntry):
    """A sourced isolation point, permit or access window, never a gate inference."""

    gate = models.ForeignKey(
        'repair.RepairPacketGate',
        on_delete=models.PROTECT,
        related_name='authority_records',
    )
    kind = models.CharField(
        max_length=24,
        choices=(
            ('isolation', 'Isolation point'),
            ('permit', 'Permit'),
            ('access_window', 'Access window'),
        ),
    )
    outcome = models.CharField(
        max_length=24,
        choices=(
            ('active', 'Active'),
            ('released', 'Released'),
            ('revoked', 'Revoked'),
        ),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(AuthorityEntry.Meta):
        """Gate ownership remains explicit and indexed."""

        abstract = False
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=('isolation', 'permit', 'access_window')),
                name='repair_authority_kind',
            )
        ]


def authority_blocker(gate, *, now=None):
    """Recorded expired/unverified/released authority cannot satisfy a gate."""
    from django.utils import timezone

    now = now or timezone.now()
    from tasks.services.operational_authority import _basis, _gate_basis

    for row in gate.authority_records.filter(correction__isnull=True):
        if (
            row.work_order_id != gate.packet.work_order_id
            or row.basis.get('machine_id') != gate.packet.machine_id
        ):
            return 'recorded authority packet ownership changed'
        if {key: row.basis.get(key) for key in ('machine_id', 'scope_ids')} != _basis(
            row.work_order
        ):
            return 'recorded authority scope changed or unrecorded'
        if row.basis.get('gate') != _gate_basis(gate):
            return 'recorded authority safety requirements changed or unrecorded'
        if row.outcome != 'active':
            return 'recorded authority released or revoked'
        if (
            row.effective_from > now
            or row.effective_until is None
            or row.effective_until <= now
        ):
            return 'recorded authority outside its effective window'
        if gate.requires_second_person and (
            not row.verified_by_id or row.verified_by_id == row.recorded_by_id
        ):
            return 'recorded authority requires independent verification'
    return ''


def gate_reason_for_actor(gate, actor, reason):
    """Readiness is public within Work scope; hidden authority details are not."""
    from tasks.scope import scoped_machines

    packet = gate.packet
    visible = bool(
        actor
        and packet.machine_id
        and (
            not packet.work_order_id
            or packet.work_order.machine_id == packet.machine_id
        )
        and scoped_machines(actor).filter(pk=packet.machine_id).exists()
    )
    return reason if visible or not reason else 'not satisfied'
