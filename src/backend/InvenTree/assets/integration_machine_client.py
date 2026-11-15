"""Unwired current machine-client edge with independent endpoint authorization.

The caller must bind the authenticated actor and server-configured environment.
The edge is not authority to read client fields, related machines or history.
"""

from dataclasses import dataclass, field

from assets.integration_contracts import ClientSourceIdentity, MachineSourceIdentity
from assets.integration_sources import _authorized_client, _authorized_machine_source


@dataclass(frozen=True)
class MachineClientSourceRecord:
    """One currently authorized machine and its independently visible client."""

    machine: MachineSourceIdentity
    client: ClientSourceIdentity
    schema_version: str = field(default='aimms.machine.client.v1', init=False)

    def __post_init__(self):
        """Reject malformed and cross-environment endpoints."""
        if (
            type(self.machine) is not MachineSourceIdentity
            or type(self.client) is not ClientSourceIdentity
            or self.machine.environment != self.client.environment
        ):
            raise ValueError('Invalid machine-client edge')


def read_machine_client(actor, *, environment, machine_pk):
    """Return a direct edge only while both endpoint checks and association hold.

    Machine scope and client visibility are separate; neither missing authority
    nor a clientless machine is represented as a known empty relationship.
    Sequential rechecks are not an atomic snapshot or revocation guarantee.
    """
    machine_identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    machine = _authorized_machine_source(actor, machine_identity)
    if machine is None or machine.client_id is None:
        return None
    client_identity = ClientSourceIdentity(
        environment=environment, pk=machine.client_id
    )
    if _authorized_client(actor, client_identity) is None:
        return None
    current = _authorized_machine_source(actor, machine_identity)
    if current is None or current.client_id != client_identity.pk:
        return None
    if _authorized_client(actor, client_identity) is None:
        return None
    current = _authorized_machine_source(actor, machine_identity)
    if current is None or current.client_id != client_identity.pk:
        return None
    return MachineClientSourceRecord(machine_identity, client_identity)
