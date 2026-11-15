"""Typed private change coordinates, never actor or deletion authority.

These carriers do not persist state, register a writer, or enable a consumer.
A coordinate or revision alone proves neither source coverage nor incarnation.
"""

from dataclasses import dataclass, field
from uuid import UUID


def literal(value, maximum):
    """Require a bounded exact string without repairing its spelling."""
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError('A bounded literal source coordinate is required')


def positive_id(value):
    """Reject bool, coercion and custom comparison aliases before identity use."""
    if type(value) is not int or value < 1:
        raise ValueError('An exact positive identifier is required')


def exact_epoch(value):
    """Require the actual nonzero UUID rather than a display-label alias."""
    if type(value) is not UUID or value.int == 0:
        raise ValueError('An exact nonzero source epoch is required')


@dataclass(frozen=True, kw_only=True)
class SourceAspectIdentity:
    """Keep source identifier types and aspect/environment epochs distinct."""

    environment: str
    epoch: UUID
    entity_kind: str
    aspect: str
    source_id: int | str = field(repr=False)

    def __post_init__(self):
        """Validate all scalar members before comparing or hashing them."""
        literal(self.environment, 128)
        exact_epoch(self.epoch)
        literal(self.entity_kind, 32)
        literal(self.aspect, 32)
        if type(self.source_id) is str:
            literal(self.source_id, 256)
        else:
            positive_id(self.source_id)
