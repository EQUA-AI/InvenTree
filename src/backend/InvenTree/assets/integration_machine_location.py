"""Unwired current machine-location edge with independent authority checks.

Callers establish the authenticated principal and server-configured environment.
This does not authorize placement history, hierarchy, counts or transfers.
"""

from dataclasses import dataclass, field
from typing import Literal

from assets.integration_contracts import LocationSourceIdentity, MachineSourceIdentity
from assets.integration_sources import _authorized_location


@dataclass(frozen=True)
class MachineLocationSourceRecord:
    """One authorized machine with a current location, null or withheld state."""

    machine: MachineSourceIdentity
    state: Literal['unassigned', 'unavailable', 'available']
    location: LocationSourceIdentity | None
    schema_version: str = field(default='aimms.machine.location.v1', init=False)

    def __post_init__(self):
        """Reject inconsistent states and cross-source identities."""
        if not isinstance(self.machine, MachineSourceIdentity):
            raise ValueError('Machine source identity is required')
        if type(self.state) is not str or self.state not in (
            'unassigned',
            'unavailable',
            'available',
        ):
            raise ValueError('Unrecognized placement state')
        if self.state == 'available':
            if not isinstance(self.location, LocationSourceIdentity):
                raise ValueError('Available placement requires a location identity')
            if self.machine.environment != self.location.environment:
                raise ValueError('Linked identities must share a source environment')
        elif self.location is not None:
            raise ValueError(
                'Unassigned or unavailable placement cannot disclose a location'
            )


@dataclass(frozen=True)
class MachinePlacementSourceRecord(MachineLocationSourceRecord):
    """An authorized placement with an incomplete native placement counter.

    The counter is not universal change, durable pending or acknowledgment
    evidence. Alternate writers may bypass it; repeated checks are not atomic.
    """

    placement_version: int
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.machine.location.v2', init=False)

    def __post_init__(self):
        """Require a real observed native version in addition to a valid edge."""
        super().__post_init__()
        if type(self.placement_version) is not int or self.placement_version < 0:
            raise ValueError('A nonnegative native placement version is required')


def _authorized_machine(actor_pk, machine_pk):
    """Fetch a current scoped machine for a freshly resolved active actor."""
    from django.contrib.auth import get_user_model

    from tasks.scope import scoped_machines

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.integration_authority import explicitly_granted_client_ids

    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    return (
        scoped_machines(user)
        .filter(pk=machine_pk, client_id__in=explicitly_granted_client_ids(user))
        .first()
    )


def read_machine_location(actor, *, environment, machine_pk):
    """Read current placement or withhold it when the machine is inaccessible.

    ``unavailable`` does not expose the referenced location and covers denied,
    missing, or inconsistent cross-client targets. Sequential checks are not an
    atomic snapshot and cannot guarantee revocation after the final check.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    initial = _authorized_machine(actor_pk, identity.pk)
    if initial is None:
        return None
    observed = (initial.client_id, initial.physical_location_id)
    location = None
    location_identity = None
    if initial.physical_location_id is not None:
        location_identity = LocationSourceIdentity(
            environment=environment, pk=initial.physical_location_id
        )
        location = _authorized_location(actor, location_identity)
        if location is not None and location.client_id != initial.client_id:
            location = None

    current = _authorized_machine(actor_pk, identity.pk)
    if current is None or (current.client_id, current.physical_location_id) != observed:
        return None
    if location_identity is None:
        return MachineLocationSourceRecord(identity, 'unassigned', None)
    if location is None:
        return MachineLocationSourceRecord(identity, 'unavailable', None)

    current_location = _authorized_location(actor, location_identity)
    if current_location is None or current_location.client_id != initial.client_id:
        return None
    current = _authorized_machine(actor_pk, identity.pk)
    if current is None or (current.client_id, current.physical_location_id) != observed:
        return None
    return MachineLocationSourceRecord(identity, 'available', location_identity)


def read_machine_placement(actor, *, environment, machine_pk):
    """Add observed version evidence without changing the legacy v1 edge.

    Independently checked endpoints and null/withheld semantics come from v1.
    Fence the native client/FK/version tuple around that work, but make no
    snapshot, ABA, universal revision or post-check revocation guarantee.
    """
    identity = MachineSourceIdentity(environment=environment, pk=machine_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None
    initial = _authorized_machine(actor_pk, identity.pk)
    if initial is None:
        return None
    observed = (
        initial.client_id,
        initial.physical_location_id,
        initial.placement_version,
    )
    edge = read_machine_location(actor, environment=environment, machine_pk=machine_pk)
    if edge is None:
        return None
    current = _authorized_machine(actor_pk, identity.pk)
    if (
        current is None
        or (current.client_id, current.physical_location_id, current.placement_version)
        != observed
    ):
        return None
    return MachinePlacementSourceRecord(
        machine=edge.machine,
        state=edge.state,
        location=edge.location,
        placement_version=current.placement_version,
    )
