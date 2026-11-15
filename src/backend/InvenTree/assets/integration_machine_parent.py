"""Unwired direct machine-parent edge with independent current authority.

Callers must bind the authenticated actor and server-configured environment.
This does not authorize the parent's details, hierarchy, history or descendants.
"""

from dataclasses import dataclass, field
from typing import Literal

from assets.integration_contracts import MachineSourceIdentity
from assets.integration_sources import _authorized_machine_source


@dataclass(frozen=True)
class MachineParentSourceRecord:
    """An authorized direct parent edge or a confirmed parentless machine."""

    machine: MachineSourceIdentity
    state: Literal['root', 'available']
    parent: MachineSourceIdentity | None
    schema_version: str = field(default='aimms.machine.parent.v1', init=False)

    def __post_init__(self):
        """Reject inconsistent or cross-environment relationships."""
        if (
            type(self.machine) is not MachineSourceIdentity
            or type(self.state) is not str
        ):
            raise ValueError('Invalid machine parent record')
        if self.state == 'root':
            if self.parent is not None:
                raise ValueError('A root cannot disclose a parent')
        elif self.state == 'available':
            if type(self.parent) is not MachineSourceIdentity:
                raise ValueError('An available edge requires a parent identity')
            if (
                self.parent.environment != self.machine.environment
                or self.parent.pk == self.machine.pk
            ):
                raise ValueError('Invalid machine parent identity')
        else:
            raise ValueError('Unrecognized machine parent state')


def read_machine_parent(actor, *, environment, machine_pk):
    """Return a direct edge only while both endpoint authorizations hold.

    Inaccessible or inconsistent endpoints return None rather than a root or
    an existence-bearing withheld marker. Sequential checks are not atomic and
    cannot establish a historical edge, prevent ABA changes or close races.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, identity)
    if machine is None:
        return None
    observed = (machine.client_id, machine.parent_id)
    if machine.parent_id is None:
        current = _authorized_machine_source(actor, identity)
        if current is None or (current.client_id, current.parent_id) != observed:
            return None
        return MachineParentSourceRecord(identity, 'root', None)
    if machine.parent_id == machine.pk:
        return None
    parent_identity = MachineSourceIdentity(
        environment=environment, pk=machine.parent_id
    )
    parent = _authorized_machine_source(actor, parent_identity)
    if parent is None or parent.client_id != machine.client_id:
        return None
    current = _authorized_machine_source(actor, identity)
    if current is None or (current.client_id, current.parent_id) != observed:
        return None
    parent = _authorized_machine_source(actor, parent_identity)
    if parent is None or parent.client_id != machine.client_id:
        return None
    current = _authorized_machine_source(actor, identity)
    if current is None or (current.client_id, current.parent_id) != observed:
        return None
    return MachineParentSourceRecord(identity, 'available', parent_identity)
