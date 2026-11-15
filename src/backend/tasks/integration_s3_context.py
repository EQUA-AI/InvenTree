"""Explicit Work context; parent proof never supplies related-machine authority."""

from dataclasses import dataclass, field, fields
from typing import cast

from assets.integration_contracts import MachineSourceIdentity
from assets.integration_sources import read_machine
from tasks.integration_s3_details import (
    DirectDetailsUnavailableError,
    WorkDetailsSourceRecord,
    read_work_details,
)

CONTEXT_FIELDS = ('machine_state', 'machine_pk', 'machine_name', 'machine_active')


@dataclass(frozen=True, kw_only=True)
class WorkContextSourceRecord(WorkDetailsSourceRecord):
    """One proven Work plus a separately authorized current machine observation."""

    machine_state: str
    machine_pk: int | None
    machine_name: str | None = field(repr=False)
    machine_active: bool | None
    schema_version: str = field(default='aimms.work.context.v1', init=False)
    withheld_fields: tuple[str, ...] = field(
        default=(
            *(
                name
                for name in cast(
                    tuple[str, ...],
                    WorkDetailsSourceRecord.__dataclass_fields__[
                        'withheld_fields'
                    ].default,
                )
                if name != 'machine'
            ),
            'machine_details',
            'machine_history',
            'machine_originals',
        ),
        init=False,
    )

    def __post_init__(self):
        """Unknown is not a null relationship and cannot carry a target identity."""
        super().__post_init__()
        if type(self.machine_state) is not str or self.machine_state not in (
            'none',
            'unknown',
            'available',
        ):
            raise ValueError('Invalid machine relationship state')
        if self.machine_state == 'available':
            if (
                type(self.machine_pk) is not int
                or self.machine_pk < 1
                or type(self.machine_name) is not str
                or type(self.machine_active) is not bool
            ):
                raise ValueError('Invalid authorized machine observation')
        elif any(
            value is not None
            for value in (self.machine_pk, self.machine_name, self.machine_active)
        ):
            raise ValueError('Unavailable relationships cannot disclose a machine')


def read_work_context(actor, *, environment, work_pk):
    """Recheck both endpoints and their edge around detached field projection."""
    from tasks.models import WorkOrder

    def parent():
        return read_work_details(actor, environment=environment, work_pk=work_pk)

    core = parent()
    if core is None:
        return None
    edge = WorkOrder.objects.filter(pk=work_pk).values_list('machine_id').first()
    if edge is None:
        return None
    machine_pk = edge[0]

    def target():
        if machine_pk is None:
            return None
        value = read_machine(actor, environment=environment, machine_pk=machine_pk)
        if value is not None and (
            type(value.identity) is not MachineSourceIdentity
            or value.identity.environment != environment
            or value.identity.pk != machine_pk
        ):
            raise DirectDetailsUnavailableError('Work context is unavailable')
        return value

    machine = target()

    def current():
        if (
            parent() != core
            or WorkOrder.objects.filter(pk=work_pk).values_list('machine_id').first()
            != edge
        ):
            return False
        if target() != machine:
            return False
        return (
            parent() == core
            and WorkOrder.objects.filter(pk=work_pk).values_list('machine_id').first()
            == edge
        )

    try:
        result = WorkContextSourceRecord(
            **{f.name: getattr(core, f.name) for f in fields(core) if f.init},
            machine_state='none'
            if machine_pk is None
            else ('unknown' if machine is None else 'available'),
            machine_pk=None if machine is None else machine.identity.pk,
            machine_name=None if machine is None else machine.name,
            machine_active=None if machine is None else machine.active,
        )
    except (TypeError, ValueError):
        if not current():
            return None
        raise DirectDetailsUnavailableError('Work context is unavailable') from None
    return result if current() else None
