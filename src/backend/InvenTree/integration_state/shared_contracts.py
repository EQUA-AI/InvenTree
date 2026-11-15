"""Private typed shared-state coordinates, never native or deletion authority."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

SHARED_SCHEMA = 'aimms.shared-state.v1'


def literal(value, maximum):
    """Reject scalar aliases without normalizing accepted source spelling."""
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError('A bounded exact source literal is required')
    if '\x00' in value:
        raise ValueError('A database-compatible source literal is required')
    value.encode('utf-8')


def integer(value, *, zero=False):
    """Use database-sized exact integers, never booleans or coerced text."""
    if type(value) is not int or not (0 if zero else 1) <= value < 2**63:
        raise ValueError('An exact bounded source integer is required')


def exact_uuid(value):
    """Keep lease/configuration epochs exact and nonzero."""
    if type(value) is not UUID or value.int == 0:
        raise ValueError('An exact nonzero ownership epoch is required')


def supported_aspects(value):
    """Configuration is an immutable bounded set of exact aspect labels."""
    if type(value) is not tuple or not 1 <= len(value) <= 64:
        raise ValueError('A bounded exact aspect tuple is required')
    for pair in value:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError('An exact entity/aspect pair is required')
        for member in pair:
            literal(member, 32)
    if len(set(value)) != len(value):
        raise ValueError('Configured aspects cannot repeat')


@dataclass(frozen=True, kw_only=True)
class ConsumerConfiguration:
    """Private server configuration, never an end-user disclosure capability."""

    consumer_id: int
    namespace_id: int
    namespace_epoch: UUID
    configuration_epoch: UUID
    consumer_key: str = field(repr=False)
    aspects: tuple[tuple[str, str], ...]

    def __post_init__(self):
        """Validate configuration coordinates independently of an ORM row."""
        integer(self.consumer_id)
        integer(self.namespace_id)
        exact_uuid(self.namespace_epoch)
        exact_uuid(self.configuration_epoch)
        literal(self.consumer_key, 128)
        supported_aspects(self.aspects)


@dataclass(frozen=True, kw_only=True)
class EventClaim:
    """A detached ownership lease; it does not certify an external effect."""

    consumer_id: int
    delivery_id: int
    change_id: int
    namespace_epoch: UUID
    configuration_epoch: UUID
    ownership_fence: int
    lease_token: UUID = field(repr=False)
    worker_key: str = field(repr=False)
    expires_at: datetime
    event_payload: str = field(repr=False)
    payload_digest: str = field(repr=False)

    def __post_init__(self):
        """Require exact owned coordinates and bounded immutable payload bytes."""
        for value in (
            self.consumer_id,
            self.delivery_id,
            self.change_id,
            self.ownership_fence,
        ):
            integer(value)
        for value in (self.namespace_epoch, self.configuration_epoch, self.lease_token):
            exact_uuid(value)
        literal(self.worker_key, 128)
        if type(self.expires_at) is not datetime:
            raise ValueError('An exact ownership expiry is required')
        literal(self.event_payload, 65536)
        if (
            type(self.payload_digest) is not str
            or hashlib.sha256(self.event_payload.encode('utf-8')).hexdigest()
            != self.payload_digest
        ):
            raise ValueError('Claim payload does not match its exact digest')


@dataclass(frozen=True, kw_only=True)
class DeliveryAcknowledgement:
    """Internal event disposition only, not serving or deletion authority."""

    delivery_id: int
    change_id: int
    outcome: str
    advanced: bool
    deletion_applied: bool = field(default=False, init=False)
    erasure_complete: bool = field(default=False, init=False)

    def __post_init__(self):
        """Keep effect and completeness claims out of metadata acknowledgments."""
        integer(self.delivery_id)
        integer(self.change_id)
        if type(self.outcome) is not str or self.outcome not in (
            'applied',
            'superseded',
            'failed',
            'uncertain',
        ):
            raise ValueError('An exact delivery outcome is required')
        if (
            type(self.advanced) is not bool
            or self.deletion_applied is not False
            or self.erasure_complete is not False
        ):
            raise ValueError('An acknowledgment cannot certify a native effect')


@dataclass(frozen=True, kw_only=True)
class SharedSourceKey:
    """Keep integer/text identity and unknown source incarnation distinct."""

    namespace_id: int
    environment: str
    epoch: UUID
    entity_kind: str
    aspect: str
    source_id: int | str = field(repr=False)
    declared_incarnation: str | None = field(default=None, repr=False)

    def __post_init__(self):
        """Identity coordinates are data, not a caller-supplied authority claim."""
        integer(self.namespace_id)
        literal(self.environment, 128)
        literal(self.entity_kind, 32)
        literal(self.aspect, 32)
        if type(self.epoch) is not UUID or self.epoch.int == 0:
            raise ValueError('An exact nonzero source epoch is required')
        if type(self.source_id) is str:
            literal(self.source_id, 256)
        else:
            integer(self.source_id)
        if self.declared_incarnation is not None:
            literal(self.declared_incarnation, 256)


@dataclass(frozen=True, kw_only=True)
class SourceObservation:
    """Source-shaped metadata without population or source-removal authority."""

    state: str
    source_revision: int | str | None = field(default=None, repr=False)
    client_ids: tuple[int, ...] | None = field(default=None, repr=False)
    content_digest: str | None = field(default=None, repr=False)
    coverage_complete: bool = field(default=False, init=False)
    source_deleted: bool = field(default=False, init=False)

    def __post_init__(self):
        """Retain exact unknowns and non-authoritative source observations."""
        if type(self.state) is not str or self.state not in (
            'available',
            'missing',
            'denied',
            'incomplete',
            'unavailable',
        ):
            raise ValueError('An explicit source observation state is required')
        if self.source_revision is not None:
            if type(self.source_revision) is str:
                literal(self.source_revision, 256)
            else:
                integer(self.source_revision, zero=True)
        if self.client_ids is not None:
            if type(self.client_ids) is not tuple or len(self.client_ids) > 1000:
                raise ValueError('A bounded exact client claim is required')
            for client in self.client_ids:
                integer(client)
            if len(set(self.client_ids)) != len(self.client_ids):
                raise ValueError('Client claims cannot repeat')
        if self.content_digest is not None:
            if (
                type(self.content_digest) is not str
                or len(self.content_digest) != 64
                or any(value not in '0123456789abcdef' for value in self.content_digest)
            ):
                raise ValueError('An exact lowercase SHA-256 observation is required')
        if self.coverage_complete is not False or self.source_deleted is not False:
            raise ValueError('An observation cannot certify completeness or deletion')


@dataclass(frozen=True, kw_only=True)
class SharedChange:
    """A private committed metadata observation, not native serving authority."""

    change_id: int
    key: SharedSourceKey = field(repr=False)
    fence: int
    observation: SourceObservation = field(repr=False)
    previous: SourceObservation | None = field(default=None, repr=False)


def key_payload(key):
    """Detach an explicit typed key without serializing arbitrary attributes."""
    if type(key) is not SharedSourceKey:
        raise ValueError('An exact shared source key is required')
    key.__post_init__()
    return {
        'schema': SHARED_SCHEMA,
        'namespace_id': key.namespace_id,
        'environment': key.environment,
        'epoch': str(key.epoch),
        'entity_kind': key.entity_kind,
        'aspect': key.aspect,
        'key_type': 'integer' if type(key.source_id) is int else 'string',
        'source_id': key.source_id,
        'declared_incarnation': key.declared_incarnation,
    }


def observation_payload(observation):
    """Explicit non-content metadata preserves unknowns and truthful limits."""
    if type(observation) is not SourceObservation:
        raise ValueError('An exact source observation is required')
    observation.__post_init__()
    return {
        'schema': SHARED_SCHEMA,
        'state': observation.state,
        'source_revision': observation.source_revision,
        'client_ids': None
        if observation.client_ids is None
        else list(observation.client_ids),
        'content_digest': observation.content_digest,
        'coverage_complete': False,
        'source_deleted': False,
    }


def observation_from_payload(payload):
    """Refuse malformed stored metadata rather than defaulting it into a value."""
    keys = {
        'schema',
        'state',
        'source_revision',
        'client_ids',
        'content_digest',
        'coverage_complete',
        'source_deleted',
    }
    if (
        type(payload) is not dict
        or set(payload) != keys
        or payload['schema'] != SHARED_SCHEMA
    ):
        raise ValueError('Stored source observation shape is unqualified')
    if (
        payload['coverage_complete'] is not False
        or payload['source_deleted'] is not False
    ):
        raise ValueError('Stored source observation cannot confer authority')
    clients = payload['client_ids']
    if clients is not None and type(clients) is not list:
        raise ValueError('Stored source scope shape is unqualified')
    return SourceObservation(
        state=payload['state'],
        source_revision=payload['source_revision'],
        client_ids=None if clients is None else tuple(clients),
        content_digest=payload['content_digest'],
    )
