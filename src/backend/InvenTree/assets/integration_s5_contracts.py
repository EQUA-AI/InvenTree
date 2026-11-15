"""Placement-only observations; these coordinates never grant authority."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from assets.integration_contracts import (
    ClientSourceIdentity,
    LocationSourceIdentity,
    MachineSourceIdentity,
)


@dataclass(frozen=True)
class PlacementExpectation:
    """An environment-qualified native placement and committed change fence."""

    machine: MachineSourceIdentity
    client: ClientSourceIdentity
    location: LocationSourceIdentity | None
    native_version: int
    fence: int
    namespace_epoch: UUID

    def __post_init__(self):
        """Keep native zero distinct from a missing or coerced revision."""
        if type(self.native_version) is not int or self.native_version < 0:
            raise ValueError('An exact nonnegative native version is required')
        if type(self.fence) is not int or self.fence < 1:
            raise ValueError('An exact positive committed change fence is required')
        coordinates = [
            (self.machine, MachineSourceIdentity),
            (self.client, ClientSourceIdentity),
        ]
        if self.location is not None:
            coordinates.append((self.location, LocationSourceIdentity))
        for coordinate, kind in coordinates:
            if type(coordinate) is not kind:
                raise ValueError('An exact typed source identity is required')
            kind(coordinate.environment, coordinate.pk)
            if coordinate.environment != self.machine.environment:
                raise ValueError('All placement coordinates must share one environment')
        if type(self.namespace_epoch) is not UUID or self.namespace_epoch.int == 0:
            raise ValueError('A nonzero typed source namespace epoch is required')


@dataclass(frozen=True)
class ProjectionBinding:
    """Server-owned configuration identity, never an actor's disclosure grant."""

    environment: str
    namespace_id: int
    namespace_epoch: UUID
    enrollment_id: int
    configuration_epoch: UUID
    projection: str
    client: ClientSourceIdentity

    def __post_init__(self):
        """Configuration labels and identities remain exact, not equality aliases."""
        for label in (self.environment, self.projection):
            if type(label) is not str or not label.strip() or len(label) > 128:
                raise ValueError('A bounded literal projection label is required')
        for identifier in (self.namespace_id, self.enrollment_id):
            if type(identifier) is not int or identifier < 1:
                raise ValueError('An exact positive projection identifier is required')
        for epoch in (self.namespace_epoch, self.configuration_epoch):
            if type(epoch) is not UUID or epoch.int == 0:
                raise ValueError('A nonzero typed projection epoch is required')
        if type(self.client) is not ClientSourceIdentity:
            raise ValueError('An exact typed projection client is required')
        ClientSourceIdentity(self.client.environment, self.client.pk)
        if self.client.environment != self.environment:
            raise ValueError('Projection client must share its source environment')


@dataclass(frozen=True)
class PlacementServingObservation:
    """A trusted adapter's observed placement in its actually serving generation."""

    binding: ProjectionBinding
    placement: PlacementExpectation
    generation: str

    def __post_init__(self):
        """A claimed generation cannot replace missing or foreign placement facts."""
        if type(self.binding) is not ProjectionBinding:
            raise ValueError('An exact typed projection binding is required')
        if type(self.placement) is not PlacementExpectation:
            raise ValueError('An exact typed placement is required')
        self.binding.__post_init__()
        self.placement.__post_init__()
        if (
            type(self.generation) is not str
            or not self.generation.strip()
            or len(self.generation) > 256
        ):
            raise ValueError('A bounded literal serving generation is required')
        if (
            self.binding.environment != self.placement.machine.environment
            or self.binding.namespace_epoch != self.placement.namespace_epoch
            or self.binding.client != self.placement.client
        ):
            raise ValueError('Serving placement must share its projection source')


class PlacementServingAdapter(Protocol):
    """Trusted server-only serving contract; not a configured external client."""

    def observe_placement(
        self, binding: ProjectionBinding, machine: MachineSourceIdentity
    ) -> PlacementServingObservation:
        """Observe the independent actually served placement and generation."""
        ...

    def verify_observation(self, observation: PlacementServingObservation) -> bool:
        """Independently verify that exact observation, outside source locks."""
        ...
